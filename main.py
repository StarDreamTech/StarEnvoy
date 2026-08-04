from __future__ import annotations

import csv
import json
import re
import sys
import time
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from DrissionPage import Chromium, ChromiumOptions
from PySide6.QtCore import QSettings, Qt, QThread, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


CITY_CODES = {
    "全国": "100010000",
    "北京": "101010100",
    "上海": "101020100",
    "广州": "101280100",
    "深圳": "101280600",
    "杭州": "101210100",
    "南京": "101190100",
    "苏州": "101190400",
    "成都": "101270100",
    "武汉": "101200100",
    "西安": "101110100",
}
EXPERIENCES = [
    "不限",
    "在校/应届",
    "经验不限",
    "1年以内",
    "1-3年",
    "3-5年",
    "5-10年",
    "10年以上",
]
EDUCATIONS = ["不限", "初中及以下", "中专/中技", "高中", "大专", "本科", "硕士", "博士"]
SALARY_MODES = ["最低薪资不低于", "薪资范围包含", "不限"]
# 档位越大要求越高；岗位要求不高于所选档位即视为匹配。
EXPERIENCE_LEVELS = {
    "在校/应届": 0,
    "应届": 0,
    "在校": 0,
    "1年以内": 1,
    "1年内": 1,
    "1-3年": 2,
    "3-5年": 3,
    "5-10年": 4,
    "10年以上": 5,
}
EDUCATION_LEVELS = {
    "初中及以下": 0,
    "初中": 0,
    "中专/中技": 1,
    "中专": 1,
    "中技": 1,
    "高中": 2,
    "大专": 3,
    "本科": 4,
    "硕士": 5,
    "博士": 6,
}
CARD_SELECTORS = ".job-card-wrapper, .job-card-box, li.job-card-wrapper"
PROJECT_DIR = Path(__file__).resolve().parent
USER_DATA_PATH = PROJECT_DIR / "user_data"
CONTACT_HISTORY_PATH = PROJECT_DIR / "data" / "contact_history.jsonl"
BOSS_DIGITS = str.maketrans(
    {
        "\ue031": "0",
        "\ue032": "1",
        "\ue033": "2",
        "\ue034": "3",
        "\ue035": "4",
        "\ue036": "5",
        "\ue037": "6",
        "\ue038": "7",
        "\ue039": "8",
        "\ue03a": "9",
    }
)


@dataclass(frozen=True)
class SearchOptions:
    city: str
    city_code: str
    query: str
    title_keywords: tuple[str, ...]
    keyword_mode: str
    salary_k: int
    salary_mode: str
    experience: str
    education: str
    max_results: int
    max_scrolls: int
    auto_contact: bool
    contact_interval: int


@dataclass(frozen=True)
class Job:
    title: str
    salary: str
    area: str
    experience: str
    education: str
    company: str
    tags: str
    link: str
    contact_status: str = "未沟通"


SALARY_UNIT_SCALE = {"K": 1.0, "千": 1.0, "W": 10.0, "万": 10.0}
NON_MONTHLY_RE = re.compile(r"/\s*(天|日|时|小时|周|年)")
SALARY_RANGE_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*([KW千万]?)\s*[-~～至－—]\s*(\d+(?:\.\d+)?)\s*([KW千万]?)"
)
SALARY_SINGLE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*([KW千万])")


def _salary_to_k(value: float, unit: str) -> float | None:
    """把带单位的薪资数值换算成 K（千元），无法确定单位时返回 None。"""
    if unit:
        return value * SALARY_UNIT_SCALE[unit]
    # 没有单位时只接受“12000-17000元/月”这类整元写法。
    return value / 1000 if value >= 1000 else None


def parse_monthly_salary(text: str) -> tuple[float, float] | None:
    """把岗位薪资文案解析为以 K（千元）为单位的月薪区间。"""
    normalized = re.sub(r"\s+", "", text.upper().replace("Ｋ", "K"))
    if NON_MONTHLY_RE.search(normalized):
        # 日结、时薪、年薪无法与月薪直接比较。
        return None

    match = SALARY_RANGE_RE.search(normalized)
    if match:
        low_text, low_unit, high_text, high_unit = match.groups()
        fallback = low_unit or high_unit
        low = _salary_to_k(float(low_text), low_unit or fallback)
        high = _salary_to_k(float(high_text), high_unit or fallback)
        if low is None or high is None:
            return None
        return (low, high) if low <= high else (high, low)

    match = SALARY_SINGLE_RE.search(normalized)
    if match:
        value = _salary_to_k(float(match.group(1)), match.group(2))
        return None if value is None else (value, value)
    return None


def decode_boss_text(text: str) -> str:
    return text.translate(BOSS_DIGITS)


def parse_keywords(text: str) -> tuple[str, ...]:
    return tuple(word.strip() for word in re.split(r"[,，]", text) if word.strip())


def _match_level(text: str, levels: dict[str, int]) -> int | None:
    """从岗位要求文案中读出档位，优先匹配更长的标签（如“1-3年”先于“1年”）。"""
    for label in sorted(levels, key=len, reverse=True):
        if label in text:
            return levels[label]
    return None


def _level_rejection_reason(
    name: str, job_text: str, selected: str, levels: dict[str, int]
) -> str | None:
    """比较岗位要求与所选档位；岗位要求不高于所选即通过。"""
    if selected == "不限":
        return None
    text = job_text or ""
    if selected not in levels:
        # 例如“经验不限”这类只按原文匹配的选项。
        if selected in text:
            return None
        return f"{name}不符：读取 {text or '空'}，要求 {selected}"
    if "不限" in text:
        # 岗位本身不限，任何档位都可投递。
        return None
    job_level = _match_level(text, levels)
    if job_level is None:
        return f"{name}无法识别：读取 {text or '空'}，要求 {selected}"
    if job_level > levels[selected]:
        return f"{name}不符：岗位要求 {text}，高于 {selected}"
    return None


def job_rejection_reason(job: Job, options: SearchOptions) -> str | None:
    title = job.title.casefold()
    keyword_hits = [keyword.casefold() in title for keyword in options.title_keywords]
    if keyword_hits and (
        (options.keyword_mode == "包含全部关键词" and not all(keyword_hits))
        or (options.keyword_mode == "包含任一关键词" and not any(keyword_hits))
    ):
        return f"岗位名未满足关键词：{', '.join(options.title_keywords)}"

    salary = parse_monthly_salary(job.salary)
    if options.salary_mode != "不限":
        if salary is None:
            return f"无法解析薪资：{job.salary or '空'}"
        low, high = salary
        if options.salary_mode == "最低薪资不低于" and low < options.salary_k:
            return f"最低薪资 {low:g}K 低于 {options.salary_k}K"
        if options.salary_mode == "薪资范围包含" and not low <= options.salary_k <= high:
            return f"{options.salary_k}K 不在 {low:g}-{high:g}K 范围内"

    return _level_rejection_reason(
        "经验", job.experience, options.experience, EXPERIENCE_LEVELS
    ) or _level_rejection_reason(
        "学历", job.education, options.education, EDUCATION_LEVELS
    )


def job_matches(job: Job, options: SearchOptions) -> bool:
    return job_rejection_reason(job, options) is None


def append_contact_history(
    job: Job, options: SearchOptions, path: Path = CONTACT_HISTORY_PATH
) -> None:
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    record = {
        "recorded_at": now,
        "contacted_at": now if job.contact_status == "已发起沟通" else None,
        "status": job.contact_status,
        "title": job.title,
        "salary": job.salary,
        "area": job.area,
        "experience": job.experience,
        "education": job.education,
        "company": job.company,
        "tags": job.tags,
        "link": job.link,
        "search": {
            "city": options.city,
            "query": options.query,
            "title_keywords": list(options.title_keywords),
            "keyword_mode": options.keyword_mode,
            "salary_k": options.salary_k,
            "salary_mode": options.salary_mode,
            "experience": options.experience,
            "education": options.education,
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")


class JobWorker(QThread):
    job_found = Signal(object)
    status_changed = Signal(str)
    failed = Signal(str)
    completed = Signal(int)
    log_message = Signal(str)

    def __init__(self, options: SearchOptions) -> None:
        super().__init__()
        self.options = options
        self._stop_requested = False

    def stop(self) -> None:
        self._stop_requested = True

    def run(self) -> None:
        browser: Chromium | None = None
        try:
            self.status_changed.emit("正在启动浏览器...")
            co = ChromiumOptions()
            co.set_user_data_path(str(USER_DATA_PATH))
            co.set_local_port(9222)
            browser = Chromium(addr_or_opts=co)
            url = (
                "https://www.zhipin.com/web/geek/jobs"
                f"?query={quote(self.options.query)}&city={self.options.city_code}"
            )
            page = browser.new_tab(url)
            self.status_changed.emit(
                "等待岗位列表；如浏览器要求登录或验证，请在浏览器中完成。"
            )
            if not self._wait_for_cards(page):
                if self._stop_requested:
                    self.completed.emit(0)
                    return
                raise RuntimeError(
                    "两分钟内未读取到岗位列表。请确认已登录并完成安全验证，然后重试。"
                )

            seen: set[str] = set()
            matched = 0
            unchanged_rounds = 0
            previous_count = 0
            self.log_message.emit(
                "[筛选条件] "
                f"地区={self.options.city} | 搜索词={self.options.query!r} | "
                f"关键词={', '.join(self.options.title_keywords) or '无'} | "
                f"模式={self.options.keyword_mode} | 薪资={self.options.salary_mode} {self.options.salary_k}K | "
                f"经验={self.options.experience} | 学历={self.options.education}"
            )

            for round_number in range(self.options.max_scrolls + 1):
                if self._stop_requested:
                    break
                cards = self._read_cards(page)
                for data in cards:
                    raw_salary = data.get("salary", "")
                    data = {
                        key: decode_boss_text(value) if isinstance(value, str) else value
                        for key, value in data.items()
                    }
                    job = Job(**data)
                    identity = job.link or f"{job.title}|{job.company}|{job.salary}"
                    if identity in seen:
                        continue
                    seen.add(identity)
                    raw_display = raw_salary.encode("unicode_escape").decode("ascii")
                    self.log_message.emit(
                        f"[读取] 岗位={job.title!r} | 薪资原文={raw_display!r} | "
                        f"薪资解码={job.salary!r} | 地区={job.area!r} | "
                        f"经验={job.experience!r} | 学历={job.education!r} | "
                        f"公司={job.company!r} | 标签={job.tags!r}"
                    )
                    reason = job_rejection_reason(job, self.options)
                    if reason:
                        self.log_message.emit(f"[跳过] {job.title} | 原因={reason}")
                        continue

                    self.log_message.emit(f"[匹配] {job.title} | {job.salary}")
                    if self.options.auto_contact:
                        self.status_changed.emit(
                            f"匹配 {job.title}，正在发起沟通..."
                        )
                        job = replace(job, contact_status=self._contact(page, job))
                        self.log_message.emit(
                            f"[沟通] {job.title} | 状态={job.contact_status}"
                        )
                        if job.contact_status in {"已发起沟通", "此前已沟通"}:
                            try:
                                append_contact_history(job, self.options)
                                self.log_message.emit(
                                    f"[记录] 已写入沟通历史：{CONTACT_HISTORY_PATH}"
                                )
                            except OSError as exc:
                                self.log_message.emit(f"[记录失败] {exc}")
                    self.job_found.emit(job)
                    matched += 1
                    if matched >= self.options.max_results:
                        break
                    if self.options.auto_contact and job.contact_status == "已发起沟通":
                        self._wait_contact_interval()
                self.status_changed.emit(
                    f"已读取 {len(seen)} 个岗位，匹配 {matched} 个（第 {round_number + 1} 轮）"
                )
                if matched >= self.options.max_results:
                    break

                unchanged_rounds = unchanged_rounds + 1 if len(seen) == previous_count else 0
                if unchanged_rounds >= 3:
                    break
                previous_count = len(seen)
                page.run_js(
                    "const box=document.querySelector('.job-list-container,.job-list-box');"
                    "if(box){box.scrollTop=box.scrollHeight;}"
                    "window.scrollTo(0,document.body.scrollHeight);"
                )
                time.sleep(1.5)

            self.completed.emit(matched)
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            # Keep the browser open so the user can inspect results and retain login state.
            browser = None

    def _wait_for_cards(self, page: object) -> bool:
        for _ in range(120):
            if self._stop_requested:
                return False
            try:
                if page.eles(f"css:{CARD_SELECTORS}", timeout=0.2):
                    return True
            except Exception:
                pass
            time.sleep(1)
        return False

    @staticmethod
    def _read_cards(page: object) -> list[dict[str, str]]:
        script = r"""
return Array.from(document.querySelectorAll('.job-card-wrapper, .job-card-box, li.job-card-wrapper')).map(card => {
  const text = selector => (card.querySelector(selector)?.innerText || '').trim();
  const texts = selector => Array.from(card.querySelectorAll(selector))
    .map(node => (node.innerText || '').trim()).filter(Boolean);
  const details = texts('.tag-list li, .job-info .tag-list li, .job-card-info li');
  const link = card.querySelector('a[href*=\"/job_detail/\"], a.job-card-left, a[href]');
  return {
    title: text('.job-name, [class*=\"job-name\"]'),
    salary: text('.salary, [class*=\"salary\"]'),
    area: text('.job-area, .job-address, .company-location, [class*=\"area\"]'),
    experience: details.find(value => /经验|应届|年以内|\d+-\d+年|年以上/.test(value)) || '',
    education: details.find(value => /初中|中专|中技|高中|大专|本科|硕士|博士|学历不限/.test(value)) || '',
    company: text('.company-name, .boss-name, [class*=\"company-name\"]'),
    tags: details.join(' / '),
    link: link ? new URL(link.getAttribute('href'), location.origin).href : ''
  };
}).filter(job => job.title);
"""
        return page.run_js(script) or []

    def _wait_contact_interval(self) -> None:
        for _ in range(self.options.contact_interval * 2):
            if self._stop_requested:
                return
            time.sleep(0.5)

    @staticmethod
    def _contact(page: object, job: Job) -> str:
        selected = page.run_js(
            r"""
const targetUrl = arguments[0];
const card = Array.from(document.querySelectorAll('.job-card-wrapper, .job-card-box, li.job-card-wrapper'))
  .find(item => {
    const link = item.querySelector('a[href*="/job_detail/"], a.job-card-left, a[href]');
    return link && new URL(link.getAttribute('href'), location.origin).href === targetUrl;
  });
if (!card) return false;
(card.querySelector('a[href*="/job_detail/"], a.job-card-left, a[href]') || card).click();
return true;
""",
            job.link,
        )
        if not selected:
            return "未找到岗位卡片"

        button = page.ele("css:.op-btn-chat", timeout=8)
        if not button:
            return "未找到沟通按钮"
        label = button.text.strip()
        if "继续沟通" in label:
            return "此前已沟通"
        if "立即沟通" not in label:
            return f"未沟通（{label or '按钮不可用'}）"

        button.click()
        stay_button = page.ele("css:.greet-boss-container .cancel-btn", timeout=8)
        if not stay_button:
            return "未完成（未出现沟通结果弹窗）"
        stay_label = stay_button.text.strip()
        if "留在此页" not in stay_label:
            return f"未完成（弹窗按钮为 {stay_label or '空'}）"
        stay_button.click()
        time.sleep(1)
        return "已发起沟通"


class LoginWorker(QThread):
    opened = Signal()
    failed = Signal(str)

    def run(self) -> None:
        try:
            co = ChromiumOptions()
            co.set_user_data_path(str(USER_DATA_PATH))
            co.set_local_port(9222)
            browser = Chromium(addr_or_opts=co)
            browser.new_tab("https://www.zhipin.com/web/user/?ka=header-login")
            self.opened.emit()
        except Exception as exc:
            self.failed.emit(str(exc))


class MainWindow(QMainWindow):
    HEADERS = ["岗位", "薪资", "地区", "经验", "学历", "公司", "标签", "沟通状态"]

    def __init__(self) -> None:
        super().__init__()
        self.worker: JobWorker | None = None
        self.login_worker: LoginWorker | None = None
        self.jobs: list[Job] = []
        self.setWindowTitle("BossPilot 岗位筛选")
        self.setMinimumSize(980, 700)
        self.resize(1180, 790)
        self._build_ui()
        self._load_settings()

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("root")
        layout = QVBoxLayout(root)
        layout.setContentsMargins(24, 20, 24, 24)
        layout.setSpacing(14)

        header = QHBoxLayout()
        header.setSpacing(16)
        brand = QVBoxLayout()
        brand.setSpacing(2)
        title = QLabel("BossPilot")
        title.setObjectName("title")
        subtitle = QLabel("岗位采集与筛选")
        subtitle.setObjectName("subtitle")
        brand.addWidget(title)
        brand.addWidget(subtitle)
        header.addLayout(brand)
        header.addStretch()
        self.filters_toggle = QPushButton("收起筛选")
        self.filters_toggle.setCheckable(True)
        self.filters_toggle.setToolTip("展开或收起搜索与筛选条件")
        header.addWidget(self.filters_toggle)
        layout.addLayout(header)

        self.filters = QGroupBox("搜索与筛选")
        self.filters.setObjectName("filters")
        filter_layout = QHBoxLayout(self.filters)
        filter_layout.setContentsMargins(18, 22, 18, 16)
        filter_layout.setSpacing(36)

        self.city = QComboBox()
        self.city.addItems(CITY_CODES)
        self.city.setCurrentText("杭州")
        self.query = QLineEdit("Python")
        self.query.setPlaceholderText("用于 BOSS 搜索，例如 Python")
        self.keywords = QLineEdit("全栈, Python, Python后端")
        self.keywords.setPlaceholderText("逗号分隔，仅匹配岗位名称")
        self.keyword_mode = QComboBox()
        self.keyword_mode.addItems(["包含任一关键词", "包含全部关键词"])
        self.salary = QSpinBox()
        self.salary.setRange(1, 200)
        self.salary.setValue(12)
        self.salary.setSuffix(" K/月")
        self.salary_mode = QComboBox()
        self.salary_mode.addItems(SALARY_MODES)
        self.experience = QComboBox()
        self.experience.addItems(EXPERIENCES)
        self.experience.setToolTip("岗位要求不高于所选档位即视为匹配，标注“经验不限”的岗位始终通过")
        self.education = QComboBox()
        self.education.addItems(EDUCATIONS)
        self.education.setToolTip("岗位要求不高于所选学历即视为匹配，标注“学历不限”的岗位始终通过")
        self.max_results = QSpinBox()
        self.max_results.setRange(1, 1000)
        self.max_results.setValue(100)
        self.max_scrolls = QSpinBox()
        self.max_scrolls.setRange(1, 100)
        self.max_scrolls.setValue(30)
        self.auto_contact = QCheckBox("匹配后立即沟通")
        self.auto_contact.setChecked(True)
        self.contact_interval = QSpinBox()
        self.contact_interval.setRange(3, 300)
        self.contact_interval.setValue(8)
        self.contact_interval.setSuffix(" 秒")

        left_fields = [
            ("地区", self.city),
            ("搜索词", self.query),
            ("岗位名称关键词", self.keywords),
            ("关键词匹配", self.keyword_mode),
            ("最多结果", self.max_results),
            ("自动沟通", self.auto_contact),
        ]
        right_fields = [
            ("目标月薪", self.salary),
            ("薪资规则", self.salary_mode),
            ("我的工作经验", self.experience),
            ("我的学历", self.education),
            ("最多加载轮数", self.max_scrolls),
            ("沟通间隔", self.contact_interval),
        ]
        for fields in (left_fields, right_fields):
            form = QFormLayout()
            form.setContentsMargins(0, 0, 0, 0)
            form.setHorizontalSpacing(16)
            form.setVerticalSpacing(10)
            form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
            for label, widget in fields:
                widget.setMinimumWidth(230)
                form.addRow(label, widget)
            filter_layout.addLayout(form, 1)

        self.filter_controls = [widget for _, widget in left_fields + right_fields]
        layout.addWidget(self.filters)
        self.filters_summary = QLabel()
        self.filters_summary.setObjectName("filtersSummary")
        self.filters_summary.setWordWrap(True)
        self.filters_summary.hide()
        layout.addWidget(self.filters_summary)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.login_button = QPushButton("登录 BOSS")
        self.start_button = QPushButton("开始采集")
        self.start_button.setObjectName("primary")
        self.stop_button = QPushButton("停止")
        self.stop_button.setEnabled(False)
        self.export_button = QPushButton("导出 CSV")
        self.export_button.setEnabled(False)
        self.history_button = QPushButton("沟通记录")
        self.history_button.setToolTip("打开沟通历史 JSONL 文件")
        self.clear_button = QPushButton("清空结果")
        self.clear_button.setEnabled(False)
        actions.addWidget(self.login_button)
        actions.addWidget(self.start_button)
        actions.addWidget(self.stop_button)
        actions.addWidget(self.export_button)
        actions.addWidget(self.history_button)
        actions.addWidget(self.clear_button)
        actions.addStretch()
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setFixedWidth(150)
        self.progress.hide()
        actions.addWidget(self.progress)
        layout.addLayout(actions)

        self.status_panel = QFrame()
        self.status_panel.setObjectName("statusPanel")
        self.status_panel.setProperty("state", "neutral")
        status_layout = QHBoxLayout(self.status_panel)
        status_layout.setContentsMargins(12, 8, 12, 8)
        self.status = QLabel("就绪")
        self.status.setObjectName("status")
        status_layout.addWidget(self.status)
        status_layout.addStretch()
        layout.addWidget(self.status_panel)

        console_group = QGroupBox("采集控制台")
        console_layout = QVBoxLayout(console_group)
        console_layout.setContentsMargins(10, 16, 10, 10)
        self.console = QPlainTextEdit()
        self.console.setReadOnly(True)
        self.console.setMaximumBlockCount(2000)
        self.console.setMinimumHeight(130)
        self.console.setMaximumHeight(220)
        self.console.setPlaceholderText("开始采集后，这里会显示每个岗位的读取字段和筛选原因。")
        console_layout.addWidget(self.console)
        layout.addWidget(console_group)

        self.table = QTableWidget(0, len(self.HEADERS))
        self.table.setObjectName("resultsTable")
        self.table.setHorizontalHeaderLabels(self.HEADERS)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(40)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)

        results = QFrame()
        results.setObjectName("resultsPanel")
        results_layout = QVBoxLayout(results)
        results_layout.setContentsMargins(0, 0, 0, 0)
        results_layout.setSpacing(0)
        results_header = QHBoxLayout()
        results_header.setContentsMargins(14, 10, 14, 10)
        results_title = QLabel("岗位结果")
        results_title.setObjectName("sectionTitle")
        self.result_count = QLabel("0 个结果")
        self.result_count.setObjectName("resultCount")
        results_header.addWidget(results_title)
        results_header.addStretch()
        results_header.addWidget(self.result_count)
        results_layout.addLayout(results_header)

        self.results_stack = QStackedWidget()
        self.results_stack.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        empty_state = QWidget()
        empty_state.setObjectName("emptyState")
        empty_layout = QVBoxLayout(empty_state)
        empty_layout.addStretch()
        empty_title = QLabel("暂无岗位结果")
        empty_title.setObjectName("emptyTitle")
        empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(empty_title)
        empty_layout.addStretch()
        self.results_stack.addWidget(empty_state)
        self.results_stack.addWidget(self.table)
        results_layout.addWidget(self.results_stack, 1)
        layout.addWidget(results, 1)
        self.setCentralWidget(root)

        self.login_button.clicked.connect(self.open_login)
        self.filters_toggle.toggled.connect(self._set_filters_collapsed)
        self.start_button.clicked.connect(self.start_search)
        self.stop_button.clicked.connect(self.stop_search)
        self.export_button.clicked.connect(self.export_csv)
        self.history_button.clicked.connect(self.open_contact_history)
        self.clear_button.clicked.connect(self.clear_results)
        self.table.cellDoubleClicked.connect(self.open_job)

    @Slot()
    def start_search(self) -> None:
        query = self.query.text().strip()
        if not query:
            QMessageBox.warning(self, "缺少搜索词", "请输入 BOSS 搜索词。")
            return
        self._save_settings()
        keywords = parse_keywords(self.keywords.text())
        city = self.city.currentText()
        options = SearchOptions(
            city=city,
            city_code=CITY_CODES[city],
            query=query,
            title_keywords=keywords,
            keyword_mode=self.keyword_mode.currentText(),
            salary_k=self.salary.value(),
            salary_mode=self.salary_mode.currentText(),
            experience=self.experience.currentText(),
            education=self.education.currentText(),
            max_results=self.max_results.value(),
            max_scrolls=self.max_scrolls.value(),
            auto_contact=self.auto_contact.isChecked(),
            contact_interval=self.contact_interval.value(),
        )
        self.clear_results()
        self.worker = JobWorker(options)
        self.worker.job_found.connect(self.add_job)
        self.worker.status_changed.connect(self.search_status_changed)
        self.worker.log_message.connect(self.append_log)
        self.worker.failed.connect(self.search_failed)
        self.worker.completed.connect(self.search_completed)
        self.worker.finished.connect(self.thread_finished)
        self.worker.finished.connect(self.worker.deleteLater)
        self._set_running(True)
        self._set_status("正在准备采集...", "running")
        self.worker.start()

    @Slot()
    def open_login(self) -> None:
        if self.login_worker and self.login_worker.isRunning():
            return
        self.login_button.setEnabled(False)
        self._set_status("正在打开 BOSS 登录页...", "running")
        self.login_worker = LoginWorker()
        self.login_worker.opened.connect(self.login_opened)
        self.login_worker.failed.connect(self.login_failed)
        self.login_worker.finished.connect(self.login_finished)
        self.login_worker.finished.connect(self.login_worker.deleteLater)
        self.login_worker.start()

    @Slot()
    def login_opened(self) -> None:
        self._set_status("登录页已打开，请在浏览器中完成登录。", "success")
        self.append_log("[登录] 已使用 user_data 打开 BOSS 登录页。")

    @Slot(str)
    def login_failed(self, message: str) -> None:
        self._set_status(f"打开登录页失败：{message}", "error")
        self.append_log(f"[登录失败] {message}")

    @Slot()
    def login_finished(self) -> None:
        self.login_button.setEnabled(True)
        self.login_worker = None

    @Slot(str)
    def append_log(self, message: str) -> None:
        self.console.appendPlainText(message)

    @Slot()
    def stop_search(self) -> None:
        if self.worker:
            self.worker.stop()
            self._set_status("正在停止...", "running")
        self.stop_button.setEnabled(False)

    @Slot(str)
    def search_status_changed(self, message: str) -> None:
        """更新采集过程中的状态信息。"""
        self._set_status(message, "running")

    @Slot(object)
    def add_job(self, job: Job) -> None:
        self.jobs.append(job)
        row = self.table.rowCount()
        self.table.insertRow(row)
        values = [
            job.title,
            job.salary,
            job.area,
            job.experience,
            job.education,
            job.company,
            job.tags,
            job.contact_status,
        ]
        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            item.setToolTip(value)
            if column == 0:
                item.setData(256, job.link)
            self.table.setItem(row, column, item)
        self.export_button.setEnabled(True)
        self._update_results_state()

    @Slot(int)
    def search_completed(self, count: int) -> None:
        self._set_status(f"采集完成，共匹配 {count} 个岗位。", "success")

    @Slot(str)
    def search_failed(self, message: str) -> None:
        self._set_status(f"采集失败：{message}", "error")
        QMessageBox.warning(self, "采集失败", message)

    @Slot()
    def thread_finished(self) -> None:
        self._set_running(False)
        self.worker = None

    @Slot()
    def clear_results(self) -> None:
        self.jobs.clear()
        self.table.setRowCount(0)
        self.export_button.setEnabled(False)
        self._update_results_state()

    @Slot()
    def export_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "导出岗位", "BossPilot_jobs.csv", "CSV 文件 (*.csv)"
        )
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8-sig") as file:
            writer = csv.writer(file)
            writer.writerow([*self.HEADERS, "链接"])
            for job in self.jobs:
                writer.writerow(
                    [
                        job.title,
                        job.salary,
                        job.area,
                        job.experience,
                        job.education,
                        job.company,
                        job.tags,
                        job.contact_status,
                        job.link,
                    ]
                )
        self._set_status(f"已导出到 {path}", "success")

    @Slot()
    def open_contact_history(self) -> None:
        CONTACT_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONTACT_HISTORY_PATH.touch(exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(CONTACT_HISTORY_PATH)))

    def _set_running(self, running: bool) -> None:
        """切换采集状态，并同步控件的可用性。"""
        for control in self.filter_controls:
            control.setEnabled(not running)
        self.start_button.setEnabled(not running)
        self.stop_button.setEnabled(running)
        self.clear_button.setEnabled(bool(self.jobs) and not running)
        self.progress.setVisible(running)

    def _update_results_state(self) -> None:
        """根据结果数量切换空状态与岗位表格。"""
        count = len(self.jobs)
        self.result_count.setText(f"{count} 个结果")
        self.results_stack.setCurrentIndex(1 if count else 0)
        self.clear_button.setEnabled(bool(count) and not bool(self.worker))

    def _set_status(self, message: str, state: str = "neutral") -> None:
        """更新状态文案与对应的视觉状态。"""
        self.status.setText(message)
        self.status_panel.setProperty("state", state)
        self.status_panel.style().unpolish(self.status_panel)
        self.status_panel.style().polish(self.status_panel)

    @Slot(bool)
    def _set_filters_collapsed(self, collapsed: bool) -> None:
        self.filters.setVisible(not collapsed)
        if collapsed:
            salary = (
                "不限"
                if self.salary_mode.currentText() == "不限"
                else f"{self.salary_mode.currentText()} {self.salary.value()}K"
            )
            self.filters_summary.setText(
                f"当前筛选：{self.city.currentText()} | 搜索词：{self.query.text().strip() or '无'} | "
                f"岗位关键词：{self.keywords.text().strip() or '无'}（{self.keyword_mode.currentText()}） | "
                f"薪资：{salary} | 经验：{self.experience.currentText()} | "
                f"学历：{self.education.currentText()} | "
                f"自动沟通：{'开启' if self.auto_contact.isChecked() else '关闭'}"
            )
        self.filters_summary.setVisible(collapsed)
        self.filters_toggle.setText("展开筛选" if collapsed else "收起筛选")

    def _load_settings(self) -> None:
        settings = QSettings("BossPilot", "BossPilot")
        controls = {
            "city": self.city,
            "keyword_mode": self.keyword_mode,
            "salary_mode": self.salary_mode,
            "experience": self.experience,
            "education": self.education,
        }
        for key, control in controls.items():
            value = settings.value(f"filters/{key}")
            if value is not None and control.findText(str(value)) >= 0:
                control.setCurrentText(str(value))

        self.query.setText(settings.value("filters/query", self.query.text(), type=str))
        self.keywords.setText(
            settings.value("filters/keywords", self.keywords.text(), type=str)
        )
        self.salary.setValue(settings.value("filters/salary", self.salary.value(), type=int))
        self.max_results.setValue(
            settings.value("filters/max_results", self.max_results.value(), type=int)
        )
        self.max_scrolls.setValue(
            settings.value("filters/max_scrolls", self.max_scrolls.value(), type=int)
        )
        self.auto_contact.setChecked(
            settings.value("filters/auto_contact", True, type=bool)
        )
        self.contact_interval.setValue(
            settings.value(
                "filters/contact_interval", self.contact_interval.value(), type=int
            )
        )
        self.filters_toggle.setChecked(
            settings.value("window/filters_collapsed", False, type=bool)
        )
        geometry = settings.value("window/geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)

    def _save_settings(self) -> None:
        settings = QSettings("BossPilot", "BossPilot")
        values = {
            "city": self.city.currentText(),
            "query": self.query.text(),
            "keywords": self.keywords.text(),
            "keyword_mode": self.keyword_mode.currentText(),
            "salary": self.salary.value(),
            "salary_mode": self.salary_mode.currentText(),
            "experience": self.experience.currentText(),
            "education": self.education.currentText(),
            "max_results": self.max_results.value(),
            "max_scrolls": self.max_scrolls.value(),
            "auto_contact": self.auto_contact.isChecked(),
            "contact_interval": self.contact_interval.value(),
        }
        for key, value in values.items():
            settings.setValue(f"filters/{key}", value)
        settings.setValue("window/filters_collapsed", self.filters_toggle.isChecked())
        settings.setValue("window/geometry", self.saveGeometry())
        settings.sync()

    @Slot(int, int)
    def open_job(self, row: int, _column: int) -> None:
        item = self.table.item(row, 0)
        if item and item.data(256):
            QDesktopServices.openUrl(QUrl(item.data(256)))

    def closeEvent(self, event: object) -> None:
        self._save_settings()
        if self.login_worker and self.login_worker.isRunning():
            self.login_worker.wait(3000)
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait(3000)
        event.accept()


STYLE = """
QWidget#root { background: #f2f5f7; color: #17212b; }
QWidget { font-size: 13px; color: #17212b; }
QLabel#title { font-size: 26px; font-weight: 700; color: #12202b; }
QLabel#subtitle { color: #66727f; font-size: 13px; }
QGroupBox#filters { border: 1px solid #d7dee5; border-radius: 6px; margin-top: 10px; background: #ffffff; font-weight: 600; }
QGroupBox#filters::title { subcontrol-origin: margin; left: 14px; padding: 0 6px; color: #43505c; }
QPlainTextEdit { border: 1px solid #d3dbe2; border-radius: 4px; background: #111820; color: #d9e2ea; padding: 8px; font-family: Menlo, monospace; font-size: 11px; }
QLineEdit, QComboBox, QSpinBox { min-height: 36px; padding: 0 10px; border: 1px solid #cbd4dc; border-radius: 4px; background: #ffffff; selection-background-color: #bce3e6; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus { border: 1px solid #087f8c; }
QPushButton { min-height: 36px; padding: 0 16px; border: 1px solid #c8d1d9; border-radius: 4px; background: #ffffff; color: #33414d; font-weight: 500; }
QPushButton:hover { background: #edf3f4; border-color: #9fb8bd; }
QPushButton:pressed { background: #e1eaec; }
QPushButton:disabled { color: #9aa6b1; background: #e9eef1; border-color: #d8e0e5; }
QPushButton#primary { color: #ffffff; background: #087f8c; border-color: #087f8c; font-weight: 600; }
QPushButton#primary:hover { background: #066c77; border-color: #066c77; }
QFrame#statusPanel { border: 1px solid #d7e4e6; border-left: 3px solid #8eaeb3; border-radius: 4px; background: #edf5f5; }
QFrame#statusPanel[state=running] { border-left-color: #087f8c; background: #e7f3f4; }
QFrame#statusPanel[state=success] { border-left-color: #2e7d5a; background: #ebf6ef; }
QFrame#statusPanel[state=error] { border-left-color: #c75c5c; background: #fbeeee; }
QLabel#status { color: #52616d; }
QLabel#filtersSummary { padding: 12px 14px; border: 1px solid #d7dee5; border-radius: 4px; background: #ffffff; color: #43505c; }
QLabel#sectionTitle { font-size: 14px; font-weight: 700; color: #253541; }
QLabel#resultCount { color: #6d7b87; }
QFrame#resultsPanel { border: 1px solid #d7dee5; border-radius: 6px; background: #ffffff; }
QWidget#emptyState { background: #ffffff; }
QLabel#emptyTitle { color: #7a8792; font-size: 15px; }
QProgressBar { border: 0; border-radius: 2px; background: #dce9eb; min-height: 5px; max-height: 5px; }
QProgressBar::chunk { border-radius: 2px; background: #087f8c; }
QTableWidget#resultsTable { border: 0; background: #ffffff; alternate-background-color: #f7f9fa; gridline-color: transparent; }
QTableWidget#resultsTable::item { padding: 8px; border-bottom: 1px solid #edf0f2; }
QTableWidget#resultsTable::item:selected { color: #17212b; background: #dff1f2; }
QHeaderView::section { padding: 10px 8px; border: 0; border-bottom: 1px solid #d7dee5; background: #f1f4f6; color: #52616d; font-weight: 600; }
"""


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("BossPilot")
    app.setStyleSheet(STYLE)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
