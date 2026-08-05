from __future__ import annotations

import csv
import json
import re
import socket
import sys
import time
from base64 import b64encode
from collections.abc import Callable
from dataclasses import dataclass, fields, replace
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from urllib.request import urlopen

from DrissionPage import Chromium, ChromiumOptions
from PySide6.QtCore import QDir, QSettings, Qt, QThread, QUrl, Signal, Slot
from PySide6.QtGui import QAction, QColor, QDesktopServices, QIcon, QImage, QPixmap, QStandardItemModel
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
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
# 结果表“沟通状态”列的配色，未列出的状态按告警色显示。
CONTACT_STATUS_COLORS = {
    "已发起沟通": "#0f7a58",
    "此前已沟通": "#5a6b7b",
    "未沟通": "#8b98a5",
}
PROJECT_DIR = Path(__file__).resolve().parent
LOGO_PATH = PROJECT_DIR / "assets" / "logo.png"
CONTACT_HISTORY_PATH = PROJECT_DIR / "data" / "contact_history.jsonl"
BROWSER_PORT_PATH = PROJECT_DIR / "data" / "browser_port.txt"
BROWSER_USER_DATA_PATH = PROJECT_DIR / "data" / "browser_user_data"
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
    experiences: tuple[str, ...]
    educations: tuple[str, ...]
    max_results: int
    max_scrolls: int
    auto_contact: bool
    contact_interval: int
    fetch_detail: bool = True


@dataclass(frozen=True)
class Job:
    # 除岗位名外都给默认值：页面改版导致某个字段读不到时不应中断整次采集。
    title: str
    salary: str = ""
    area: str = ""
    experience: str = ""
    education: str = ""
    company: str = ""
    tags: str = ""
    link: str = ""
    contact_status: str = "未沟通"
    # 以下字段来自岗位卡片，描述公司概况与 HR。
    industry: str = ""
    scale: str = ""
    stage: str = ""
    hr_name: str = ""
    hr_title: str = ""
    hr_active: str = ""
    # 以下字段来自点击卡片后列表页右侧的详情面板。
    skills: str = ""
    description: str = ""
    benefits: str = ""
    address: str = ""
    detail_status: str = "未读取"


class MultiSelectComboBox(QComboBox):
    selection_changed = Signal()

    def __init__(self, items: list[str], checked: tuple[str, ...]) -> None:
        super().__init__()
        self.setEditable(True)
        self.lineEdit().setReadOnly(True)
        self.lineEdit().setPlaceholderText("不限")
        self.setModel(QStandardItemModel(self))
        self.view().pressed.connect(self._toggle_index)
        self.currentIndexChanged.connect(lambda _index: self._refresh_text())
        for text in items:
            self.addItem(text)
            item = self.model().item(self.count() - 1)
            item.setFlags(
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsUserCheckable
                | Qt.ItemFlag.ItemIsSelectable
            )
            item.setCheckState(
                Qt.CheckState.Checked if text in checked else Qt.CheckState.Unchecked
            )
        self.model().itemChanged.connect(lambda _item: self._refresh_text())
        self._refresh_text()

    def hidePopup(self) -> None:
        if self.view().underMouse():
            return
        super().hidePopup()

    def checked_texts(self) -> tuple[str, ...]:
        return tuple(
            self.itemText(index)
            for index in range(self.count())
            if self.model().item(index).checkState() == Qt.CheckState.Checked
        )

    def set_checked_texts(self, values: tuple[str, ...]) -> None:
        selected = set(values)
        for index in range(self.count()):
            self.model().item(index).setCheckState(
                Qt.CheckState.Checked
                if self.itemText(index) in selected
                else Qt.CheckState.Unchecked
            )
        self._refresh_text()

    def _toggle_index(self, index: object) -> None:
        item = self.model().item(index.row())
        item.setCheckState(
            Qt.CheckState.Unchecked
            if item.checkState() == Qt.CheckState.Checked
            else Qt.CheckState.Checked
        )

    def _refresh_text(self) -> None:
        values = self.checked_texts()
        text = "、".join(values) if values else "不限"
        self.lineEdit().setText(text)
        self.setToolTip(text)
        self.selection_changed.emit()


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


JOB_FIELDS = frozenset(field.name for field in fields(Job))


def build_job(data: dict[str, object]) -> Job:
    """把页面读到的原始字段解码后构造 Job，忽略未知字段。"""
    values = {
        key: decode_boss_text(value) if isinstance(value, str) else value
        for key, value in data.items()
        if key in JOB_FIELDS
    }
    return Job(**values)


def job_from_history_record(record: dict[str, object]) -> Job:
    detail = record.get("detail")
    detail = detail if isinstance(detail, dict) else {}
    company_profile = record.get("company_profile")
    company_profile = company_profile if isinstance(company_profile, dict) else {}
    hr = record.get("hr")
    hr = hr if isinstance(hr, dict) else {}
    return Job(
        title=str(record.get("title") or ""),
        salary=str(record.get("salary") or ""),
        area=str(record.get("area") or ""),
        experience=str(record.get("experience") or ""),
        education=str(record.get("education") or ""),
        company=str(record.get("company") or ""),
        tags=str(record.get("tags") or ""),
        link=str(record.get("link") or ""),
        contact_status=str(record.get("status") or ""),
        industry=str(company_profile.get("industry") or ""),
        scale=str(company_profile.get("scale") or ""),
        stage=str(company_profile.get("stage") or ""),
        hr_name=str(hr.get("name") or ""),
        hr_title=str(hr.get("title") or ""),
        hr_active=str(hr.get("active") or ""),
        skills=str(detail.get("skills") or ""),
        description=str(detail.get("description") or ""),
        benefits=str(detail.get("benefits") or ""),
        address=str(detail.get("address") or ""),
        detail_status=str(detail.get("status") or "未读取"),
    )


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


def devtools_alive(port: int) -> bool:
    try:
        with urlopen(f"http://127.0.0.1:{port}/json/version", timeout=0.5) as response:
            version = json.loads(response.read())
        with urlopen(f"http://127.0.0.1:{port}/json", timeout=0.5) as response:
            targets = json.loads(response.read())
        return "webSocketDebuggerUrl" in version and any(
            target.get("type") in {"page", "webview"} for target in targets
        )
    except Exception:
        return False


def free_local_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def boss_browser_options(
    port_path: Path = BROWSER_PORT_PATH,
    *,
    port_factory: Callable[[], int] = free_local_port,
    alive_check: Callable[[int], bool] = devtools_alive,
) -> ChromiumOptions:
    try:
        saved_port = int(port_path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        saved_port = 0

    port = saved_port if saved_port and alive_check(saved_port) else port_factory()
    port_path.parent.mkdir(parents=True, exist_ok=True)
    port_path.write_text(str(port), encoding="utf-8")

    co = ChromiumOptions()
    co.set_user_data_path(str(BROWSER_USER_DATA_PATH))
    co.set_local_port(port)
    return co


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


def _multi_level_rejection_reason(
    name: str, job_text: str, selected_values: tuple[str, ...], levels: dict[str, int]
) -> str | None:
    values = selected_values or ("不限",)
    reasons = [
        _level_rejection_reason(name, job_text, selected, levels)
        for selected in values
    ]
    if any(reason is None for reason in reasons):
        return None
    return f"{name}不符：读取 {job_text or '空'}，要求 {'、'.join(values)}"


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

    return _multi_level_rejection_reason(
        "经验", job.experience, options.experiences, EXPERIENCE_LEVELS
    ) or _multi_level_rejection_reason(
        "学历", job.education, options.educations, EDUCATION_LEVELS
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
        "company_profile": {
            "industry": job.industry,
            "scale": job.scale,
            "stage": job.stage,
        },
        "hr": {
            "name": job.hr_name,
            "title": job.hr_title,
            "active": job.hr_active,
        },
        "detail": {
            "status": job.detail_status,
            "skills": job.skills,
            "description": job.description,
            "benefits": job.benefits,
            "address": job.address,
        },
        "search": {
            "city": options.city,
            "query": options.query,
            "title_keywords": list(options.title_keywords),
            "keyword_mode": options.keyword_mode,
            "salary_k": options.salary_k,
            "salary_mode": options.salary_mode,
            "experiences": list(options.experiences),
            "educations": list(options.educations),
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")


class JobWorker(QThread):
    job_found = Signal(object)
    status_changed = Signal(str)
    stats_changed = Signal(int, int, int)  # 已读取、已匹配、已沟通
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
        # 采集结束后不关闭浏览器，便于用户查看结果并保留登录状态。
        try:
            self.status_changed.emit("正在启动浏览器...")
            browser = Chromium(addr_or_opts=boss_browser_options())
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
                if self._login_required(page):
                    raise RuntimeError(
                        "需要登录 BOSS：请点击“登录 BOSS”完成登录后再开始采集。"
                    )
                raise RuntimeError(
                    "两分钟内未读取到岗位列表。请确认已登录并完成安全验证，然后重试。"
                )
            if self._login_required(page):
                raise RuntimeError("需要登录 BOSS：请点击“登录 BOSS”完成登录后再开始采集。")

            seen: set[str] = set()
            matched = 0
            contacted = 0
            unchanged_rounds = 0
            previous_count = 0
            self.log_message.emit(
                "[筛选条件] "
                f"地区={self.options.city} | 搜索词={self.options.query!r} | "
                f"关键词={', '.join(self.options.title_keywords) or '无'} | "
                f"模式={self.options.keyword_mode} | 薪资={self.options.salary_mode} {self.options.salary_k}K | "
                f"经验={'、'.join(self.options.experiences) or '不限'} | "
                f"学历={'、'.join(self.options.educations) or '不限'}"
            )

            for round_number in range(self.options.max_scrolls):
                if self._stop_requested:
                    break
                try:
                    cards = self._read_cards(page)
                except Exception as exc:
                    # 单轮读取失败（页面跳转、脚本执行超时等）不应终止整次采集。
                    self.log_message.emit(f"[读取失败] 本轮跳过：{exc}")
                    cards = []
                for data in cards:
                    raw_salary = data.get("salary", "")
                    job = build_job(data)
                    identity = job.link or f"{job.title}|{job.company}|{job.salary}"
                    if identity in seen:
                        continue
                    seen.add(identity)
                    self.stats_changed.emit(len(seen), matched, contacted)
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
                    if self.options.fetch_detail:
                        self.status_changed.emit(f"匹配 {job.title}，正在读取岗位详情...")
                        job = replace(job, **self._read_detail(page, job))
                        self.log_message.emit(
                            f"[详情] {job.title} | 状态={job.detail_status} | "
                            f"技能={job.skills or '空'} | 福利={job.benefits or '空'} | "
                            f"地址={job.address or '空'} | "
                            f"HR={job.hr_name or '空'}（{job.hr_title or '未知'}，"
                            f"{job.hr_active or '活跃度未知'}） | "
                            f"描述{len(job.description)} 字"
                        )
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
                    if job.contact_status in {"已发起沟通", "此前已沟通"}:
                        contacted += 1
                    self.stats_changed.emit(len(seen), matched, contacted)
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

    @staticmethod
    def _login_required(page: object) -> bool:
        script = r"""
const text = document.body?.innerText || '';
return /登录\s*\/\s*注册|登录BOSS|立即登录/.test(text)
  || Array.from(document.querySelectorAll('a, button')).some(node =>
    /登录\s*\/\s*注册|登录BOSS|立即登录/.test((node.innerText || '').trim()));
"""
        try:
            return bool(page.run_js(script))
        except Exception:
            return False

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
  // 公司标签里混排了行业、规模与融资阶段，按特征分别挑出来。
  const companyTags = texts('.company-tag-list li, .company-info .company-tag-list li');
  const scale = companyTags.find(value => /人|规模/.test(value)) || '';
  const stage = companyTags.find(value => /融资|上市|不需要融资|天使|轮/.test(value)) || '';
  const industry = companyTags.find(value => value !== scale && value !== stage) || '';
  return {
    title: text('.job-name, [class*=\"job-name\"]'),
    salary: text('.salary, [class*=\"salary\"]'),
    area: text('.job-area, .job-address, .company-location, [class*=\"area\"]'),
    experience: details.find(value => /经验|应届|年以内|\d+-\d+年|年以上/.test(value)) || '',
    education: details.find(value => /初中|中专|中技|高中|大专|本科|硕士|博士|学历不限/.test(value)) || '',
    company: text('.company-name, .boss-name, [class*=\"company-name\"]'),
    tags: details.join(' / '),
    industry: industry,
    scale: scale,
    stage: stage,
    hr_name: text('.info-public em, .job-card-footer .info-public, [class*=\"boss-name\"]'),
    hr_title: text('.info-public'),
    link: link ? new URL(link.getAttribute('href'), location.origin).href : ''
  };
}).filter(job => job.title);
"""
        return page.run_js(script) or []

    # 列表页右侧详情面板；点击卡片后异步刷新，需要等标题变化再读取。
    DETAIL_SCRIPT = r"""
const panel = document.querySelector('.job-detail-box, .job-detail, .job-detail-container');
if (!panel) return null;
const text = selector => (panel.querySelector(selector)?.innerText || '').trim();
const texts = selector => Array.from(panel.querySelectorAll(selector))
  .map(node => (node.innerText || '').trim()).filter(Boolean);
const hrBlock = panel.querySelector('.job-boss-info, .boss-info, [class*=\"boss-info\"]');
const hrText = hrBlock ? (hrBlock.innerText || '').trim() : '';
const sectionText = (start, stops) => {
  const lines = (panel.innerText || '').split('\n').map(line => line.trim());
  const startIndex = lines.findIndex(line => line === start);
  if (startIndex < 0) return '';
  const endIndex = lines.findIndex((line, index) => index > startIndex && stops.includes(line));
  return lines.slice(startIndex + 1, endIndex < 0 ? undefined : endIndex).join('\n').trim();
};
return {
  title: text('.job-name, .name, h3'),
  skills: texts('.job-keyword-list li, .tag-list li, [class*=\"keyword\"] li').join(' / '),
  description: text('.job-sec-text, .job-detail-section .text, [class*=\"job-sec\"] .text')
    || sectionText('职位描述', ['工作地址', '去App', '前往App', '查看更多信息']),
  benefits: texts('.job-tags li, .welfare-list li, [class*=\"welfare\"] li').join(' / '),
  address: text('.location-address, .job-address .location-address, [class*=\"location-address\"]')
    || sectionText('工作地址', ['点击查看地图', '查看更多信息']),
  hr_name: text('.boss-info-attr, .boss-name, [class*=\"boss-name\"]'),
  hr_text: hrText,
  hr_active: (hrText.match(/(刚刚活跃|今日活跃|本周活跃|\d+(?:分钟|小时|天|周|月)内活跃|活跃于[^\n]*)/) || [''])[0]
};
"""

    def _read_detail(self, page: object, job: Job) -> dict[str, str]:
        """点击岗位卡片，读取列表页右侧详情面板。"""
        opened = page.run_js(
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
        if not opened:
            return {"detail_status": "未找到岗位卡片"}

        # 面板内容异步加载，轮询到标题与当前岗位一致为止。
        detail: dict[str, str] | None = None
        for _ in range(20):
            if self._stop_requested:
                return {"detail_status": "已停止"}
            time.sleep(0.4)
            try:
                candidate = page.run_js(self.DETAIL_SCRIPT)
            except Exception as exc:
                return {"detail_status": f"读取失败：{exc}"}
            if not candidate:
                continue
            detail = candidate
            panel_title = decode_boss_text(candidate.get("title") or "")
            if panel_title and (panel_title in job.title or job.title in panel_title):
                break
        if not detail:
            return {"detail_status": "未出现详情面板"}

        values = {
            key: decode_boss_text(value) if isinstance(value, str) else value
            for key, value in detail.items()
            if key in JOB_FIELDS and key != "title"
        }
        hr_text = decode_boss_text(detail.get("hr_text") or "")
        if not values.get("hr_name") and hr_text:
            values["hr_name"] = hr_text.splitlines()[0].strip()
        if hr_text:
            # HR 行形如“杭州橙风信息科技 · hr”，取分隔符后的职位。
            match = re.search(r"·\s*([^\n]+)", hr_text)
            if match:
                values["hr_title"] = match.group(1).strip()
        values["detail_status"] = "已读取" if values.get("description") else "无职位描述"
        return values

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
            browser = Chromium(addr_or_opts=boss_browser_options())
            browser.new_tab("https://www.zhipin.com/web/user/?ka=header-login")
            self.opened.emit()
        except Exception as exc:
            self.failed.emit(str(exc))


class MainWindow(QMainWindow):
    # 表格列：(标题, 取值函数)。导出 CSV 复用同一份定义。
    COLUMNS: tuple[tuple[str, str], ...] = (
        ("岗位", "title"),
        ("薪资", "salary"),
        ("地区", "area"),
        ("经验", "experience"),
        ("学历", "education"),
        ("公司", "company"),
        ("行业", "industry"),
        ("规模", "scale"),
        ("融资阶段", "stage"),
        ("HR", "hr_name"),
        ("HR 职位", "hr_title"),
        ("HR 活跃", "hr_active"),
        ("技能要求", "skills"),
        ("福利", "benefits"),
        ("工作地址", "address"),
        ("职位描述", "description"),
        ("标签", "tags"),
        ("沟通状态", "contact_status"),
    )
    HEADERS = [caption for caption, _ in COLUMNS]
    STATUS_COLUMN = HEADERS.index("沟通状态")
    DESCRIPTION_COLUMN = HEADERS.index("职位描述")
    # 默认隐藏的次要列：仍会采集并导出，可用“显示全部列”展开。
    OPTIONAL_COLUMNS = ("规模", "融资阶段", "HR 职位", "HR 活跃", "福利", "工作地址", "标签")
    # 沟通记录表格列：(标题, 从 JSONL 记录取值的函数)。
    HISTORY_COLUMNS: tuple[tuple[str, str], ...] = (
        ("时间", "recorded_at"),
        ("岗位", "title"),
        ("薪资", "salary"),
        ("地区", "area"),
        ("公司", "company"),
        ("HR", "hr"),
        ("搜索词", "query"),
        ("状态", "status"),
        ("岗位链接", "link"),
        ("跳转", "open"),
    )
    HISTORY_HEADERS = [caption for caption, _ in HISTORY_COLUMNS]
    HISTORY_STATUS_COLUMN = HISTORY_HEADERS.index("状态")
    HISTORY_OPEN_COLUMN = HISTORY_HEADERS.index("跳转")
    TAB_FILTERS, TAB_RESULTS, TAB_HISTORY, TAB_CONSOLE = range(4)
    # 状态卡下方常驻的条件胶囊：(键, 说明)，取值见 _filter_chip_values。
    FILTER_CHIPS: tuple[tuple[str, str], ...] = (
        ("city", "地区"),
        ("query", "搜索词"),
        ("keywords", "关键词"),
        ("salary", "薪资"),
        ("experience", "经验"),
        ("education", "学历"),
        ("detail", "详情"),
        ("contact", "自动沟通"),
    )

    def __init__(self) -> None:
        super().__init__()
        self.worker: JobWorker | None = None
        self.login_worker: LoginWorker | None = None
        self.jobs: list[Job] = []
        self.history_records: list[dict[str, object]] = []
        self.setWindowTitle("BossPilot 岗位筛选")
        self.setMinimumSize(980, 700)
        self.resize(1180, 790)
        self._build_ui()
        self._load_settings()

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("root")
        layout = QVBoxLayout(root)
        layout.setContentsMargins(22, 18, 22, 20)
        layout.setSpacing(12)

        layout.addLayout(self._build_header())
        # 先建标签页：状态卡里的条件胶囊要读筛选控件的值。
        tabs = self._build_tabs()
        layout.addWidget(self._build_status())
        layout.addWidget(tabs, 1)
        self.setCentralWidget(root)

        self.login_button.clicked.connect(self.open_login)
        self.salary_mode.currentTextChanged.connect(self._sync_salary_enabled)
        self._sync_salary_enabled(self.salary_mode.currentText())
        for control in self.filter_controls:
            signal = (
                getattr(control, "selection_changed", None)
                or getattr(control, "currentTextChanged", None)
                or getattr(control, "textChanged", None)
                or getattr(control, "valueChanged", None)
                or getattr(control, "toggled", None)
            )
            signal.connect(self._refresh_filter_chips)
        self._refresh_filter_chips()
        self.start_button.clicked.connect(self.start_search)
        self.stop_button.clicked.connect(self.stop_search)
        self.export_button.clicked.connect(self.export_csv)
        self.clear_button.clicked.connect(self.clear_results)
        self.table.cellDoubleClicked.connect(self.open_job)
        self.history_table.cellDoubleClicked.connect(self.show_history_detail)
        self.tabs.currentChanged.connect(self._tab_changed)

    def _build_header(self) -> QHBoxLayout:
        """顶部：品牌信息 + 全局操作按钮，任何标签页下都能看到。"""
        header = QHBoxLayout()
        header.setSpacing(12)
        logo = QLabel()
        logo.setObjectName("logo")
        logo.setPixmap(load_logo(34))
        header.addWidget(logo, 0, Qt.AlignmentFlag.AlignVCenter)
        brand = QVBoxLayout()
        brand.setSpacing(1)
        title = QLabel("BossPilot")
        title.setObjectName("title")
        subtitle = QLabel("岗位采集与筛选")
        subtitle.setObjectName("subtitle")
        brand.addWidget(title)
        brand.addWidget(subtitle)
        header.addLayout(brand)
        header.addStretch()
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setFixedWidth(140)
        self.progress.hide()
        header.addWidget(self.progress, 0, Qt.AlignmentFlag.AlignVCenter)
        self.login_button = QPushButton("登录 BOSS")
        self.login_button.setToolTip("打开 BOSS 登录页，登录状态保存在 data/browser_user_data 目录")
        self.start_button = QPushButton("开始采集")
        self.start_button.setObjectName("primary")
        self.stop_button = QPushButton("停止")
        self.stop_button.setObjectName("danger")
        self.stop_button.setEnabled(False)
        for button in (self.login_button, self.start_button, self.stop_button):
            header.addWidget(button, 0, Qt.AlignmentFlag.AlignVCenter)
        return header

    def _build_tabs(self) -> QWidget:
        """把筛选、结果、沟通记录、日志拆成四个标签页，避免挤在一屏。"""
        self.tabs = QTabWidget()
        self.tabs.setObjectName("mainTabs")
        # macOS 原生风格会把标签栏居中，文档模式下才是左对齐；
        # 因此卡片描边画在各标签页自身（QWidget#tabPage）上。
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(self._build_filters(), "采集设置")
        self.tabs.addTab(self._build_results(), "岗位结果")
        self.tabs.addTab(self._build_history(), "沟通记录")
        self.tabs.addTab(self._build_console(), "采集日志")
        self.tabs.setTabToolTip(self.TAB_FILTERS, "搜索目标、岗位条件与采集行为")
        self.tabs.setTabToolTip(self.TAB_RESULTS, "本次采集匹配到的岗位")
        self.tabs.setTabToolTip(self.TAB_HISTORY, "历史沟通记录，来自 data/contact_history.jsonl")
        self.tabs.setTabToolTip(self.TAB_CONSOLE, "每个岗位的读取字段与筛选原因")
        return self.tabs

    @staticmethod
    def _panel_header(hint: str, *widgets: QWidget) -> QHBoxLayout:
        """各标签页顶部的工具条：左侧用法提示（标题由标签页承担），右侧操作。"""
        header = QHBoxLayout()
        header.setContentsMargins(16, 10, 16, 10)
        header.setSpacing(10)
        label = QLabel(hint)
        label.setObjectName("panelHint")
        header.addWidget(label)
        header.addStretch()
        for widget in widgets:
            header.addWidget(widget)
        return header

    @staticmethod
    def _empty_state(title: str, hint: str) -> QWidget:
        empty = QWidget()
        empty.setObjectName("emptyState")
        layout = QVBoxLayout(empty)
        layout.setSpacing(6)
        layout.addStretch()
        for text, name in ((title, "emptyTitle"), (hint, "emptyHint")):
            label = QLabel(text)
            label.setObjectName(name)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(label)
        layout.addStretch()
        return empty

    def _build_filters(self) -> QWidget:
        self.filters = QWidget()
        self.filters.setObjectName("tabPage")
        page_layout = QVBoxLayout(self.filters)
        page_layout.setContentsMargins(1, 1, 1, 1)
        page_layout.setSpacing(0)
        filter_body = QWidget()
        filter_body.setObjectName("filterBody")
        filter_layout = QHBoxLayout(filter_body)
        filter_layout.setContentsMargins(20, 16, 20, 16)
        filter_layout.setSpacing(26)

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
        self.experience = MultiSelectComboBox(EXPERIENCES, ("1-3年",))
        self.experience.setToolTip("可多选；岗位要求满足任一所选经验档位即通过")
        self.education = MultiSelectComboBox(EDUCATIONS, ("本科",))
        self.education.setToolTip("可多选；岗位要求满足任一所选学历档位即通过")
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
        self.fetch_detail = QCheckBox("读取职位描述")
        self.fetch_detail.setChecked(True)
        self.fetch_detail.setToolTip(
            "点击匹配到的岗位卡片，读取列表页右侧的职位描述、技能标签、福利、地址与 HR 信息。"
            "关闭可以加快采集速度。"
        )

        groups = [
            (
                "搜索目标",
                [
                    ("地区", self.city),
                    ("搜索词", self.query),
                    ("岗位名称关键词", self.keywords),
                    ("关键词匹配", self.keyword_mode),
                ],
            ),
            (
                "岗位条件",
                [
                    ("目标月薪", self.salary),
                    ("薪资规则", self.salary_mode),
                    ("我的工作经验", self.experience),
                    ("我的学历", self.education),
                ],
            ),
            (
                "采集与沟通",
                [
                    ("最多结果", self.max_results),
                    ("最多加载轮数", self.max_scrolls),
                    ("沟通间隔", self.contact_interval),
                    ("岗位详情", self.fetch_detail),
                    ("自动沟通", self.auto_contact),
                ],
            ),
        ]
        self.filter_controls = [
            widget for _, rows in groups for _, widget in rows
        ]
        for index, (caption, rows) in enumerate(groups):
            if index:
                divider = QFrame()
                divider.setObjectName("vDivider")
                divider.setFrameShape(QFrame.Shape.VLine)
                filter_layout.addWidget(divider)
            column = QVBoxLayout()
            column.setContentsMargins(0, 0, 0, 0)
            column.setSpacing(8)
            group_label = QLabel(caption)
            group_label.setObjectName("fieldGroup")
            column.addWidget(group_label)
            form = QFormLayout()
            form.setContentsMargins(0, 0, 0, 0)
            form.setHorizontalSpacing(14)
            form.setVerticalSpacing(7)
            form.setLabelAlignment(
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            )
            form.setFieldGrowthPolicy(
                QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
            )
            for label, widget in rows:
                widget.setMinimumWidth(170)
                form.addRow(label, widget)
            column.addLayout(form)
            column.addStretch()
            filter_layout.addLayout(column, 1)

        page_layout.addWidget(filter_body)
        page_layout.addStretch()
        hint = QLabel(
            "提示：经验与学历可多选，岗位要求满足任一所选档位即视为匹配；"
            "关闭“岗位详情”可以加快采集速度。设置会自动保存，下次启动沿用。"
        )
        hint.setObjectName("pageHint")
        hint.setWordWrap(True)
        page_layout.addWidget(hint)
        return self.filters

    @staticmethod
    def _chip(caption: str) -> tuple[QFrame, QLabel]:
        """胶囊标签：左侧灰色说明，右侧深色取值。"""
        chip = QFrame()
        chip.setObjectName("statChip")
        layout = QHBoxLayout(chip)
        layout.setContentsMargins(10, 3, 10, 3)
        layout.setSpacing(6)
        caption_label = QLabel(caption)
        caption_label.setObjectName("statCaption")
        value_label = QLabel()
        value_label.setObjectName("statValue")
        layout.addWidget(caption_label)
        layout.addWidget(value_label)
        return chip, value_label

    def _build_status(self) -> QWidget:
        """状态卡：上行是采集状态与计数，下行常驻显示当前筛选条件。"""
        self.status_panel = QFrame()
        self.status_panel.setObjectName("statusPanel")
        panel_layout = QVBoxLayout(self.status_panel)
        panel_layout.setContentsMargins(14, 9, 14, 9)
        panel_layout.setSpacing(9)

        # 状态底色只染上半行，免得报错时整张卡片连筛选条件一起变红。
        self.status_row = QFrame()
        self.status_row.setObjectName("statusRow")
        status_row = QHBoxLayout(self.status_row)
        status_row.setContentsMargins(10, 5, 10, 5)
        status_row.setSpacing(10)
        self.status_dot = QLabel()
        self.status_dot.setObjectName("statusDot")
        self.status_dot.setFixedSize(8, 8)
        self.status = QLabel("就绪")
        self.status.setObjectName("status")
        status_row.addWidget(self.status_dot)
        status_row.addWidget(self.status)
        status_row.addStretch()
        self.stat_labels: dict[str, QLabel] = {}
        for key, caption in (
            ("scanned", "已读取"),
            ("matched", "已匹配"),
            ("contacted", "已沟通"),
        ):
            chip, value_label = self._chip(caption)
            value_label.setText("0")
            status_row.addWidget(chip)
            self.stat_labels[key] = value_label
        panel_layout.addWidget(self.status_row)

        divider = QFrame()
        divider.setObjectName("hDivider")
        divider.setFrameShape(QFrame.Shape.HLine)
        panel_layout.addWidget(divider)

        # 切到别的标签页时也要看得见条件，所以放在常驻的状态卡里。
        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)
        caption = QLabel("当前筛选")
        caption.setObjectName("barCaption")
        filter_row.addWidget(caption)
        self.filter_chips: dict[str, tuple[QFrame, QLabel]] = {}
        for key, chip_caption in self.FILTER_CHIPS:
            chip, value_label = self._chip(chip_caption)
            filter_row.addWidget(chip)
            self.filter_chips[key] = (chip, value_label)
        filter_row.addStretch()
        self.edit_filters_button = QPushButton("修改条件")
        self.edit_filters_button.setObjectName("mini")
        self.edit_filters_button.clicked.connect(
            lambda: self.tabs.setCurrentIndex(self.TAB_FILTERS)
        )
        filter_row.addWidget(self.edit_filters_button)
        panel_layout.addLayout(filter_row)

        self._set_status("就绪")
        return self.status_panel

    def _build_results(self) -> QWidget:
        self.table = QTableWidget(0, len(self.HEADERS))
        self.table.setObjectName("resultsTable")
        self.table.setHorizontalHeaderLabels(self.HEADERS)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.setToolTip("双击任意一行可以打开岗位详情页")
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(42)
        header = self.table.horizontalHeader()
        header.setHighlightSections(False)
        # 列数较多，改为可手动拖拽 + 预设宽度，避免描述列把表格撑爆。
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(False)
        widths = {
            "岗位": 200,
            "薪资": 110,
            "地区": 160,
            "经验": 80,
            "学历": 70,
            "公司": 180,
            "行业": 110,
            "规模": 90,
            "融资阶段": 90,
            "HR": 90,
            "HR 职位": 90,
            "HR 活跃": 90,
            "技能要求": 200,
            "福利": 160,
            "工作地址": 180,
            "职位描述": 320,
            "标签": 160,
            "沟通状态": 110,
        }
        for index, caption in enumerate(self.HEADERS):
            self.table.setColumnWidth(index, widths[caption])
            if caption in self.OPTIONAL_COLUMNS:
                self.table.setColumnHidden(index, True)

        results = QWidget()
        results.setObjectName("tabPage")
        results_layout = QVBoxLayout(results)
        results_layout.setContentsMargins(1, 1, 1, 1)
        results_layout.setSpacing(0)
        self.result_count = QLabel("0 个结果")
        self.result_count.setObjectName("resultCount")
        self.result_columns_button = self._column_menu_button(
            self.table, self.HEADERS, "result", self.OPTIONAL_COLUMNS
        )
        self.export_button = QPushButton("导出 CSV")
        self.export_button.setObjectName("mini")
        self.export_button.setEnabled(False)
        self.clear_button = QPushButton("清空结果")
        self.clear_button.setObjectName("mini")
        self.clear_button.setEnabled(False)
        results_layout.addLayout(
            self._panel_header(
                "双击一行打开岗位页，双击“职位描述”查看完整招聘要求",
                self.result_columns_button,
                self.result_count,
                self.export_button,
                self.clear_button,
            )
        )
        self.results_stack = QStackedWidget()
        self.results_stack.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.results_stack.addWidget(
            self._empty_state("暂无岗位结果", "在“采集设置”里配置条件后点击“开始采集”")
        )
        self.results_stack.addWidget(self.table)
        results_layout.addWidget(self.results_stack, 1)
        return results

    def _column_menu_button(
        self,
        table: QTableWidget,
        headers: list[str],
        settings_key: str,
        default_hidden: tuple[str, ...],
    ) -> QToolButton:
        button = QToolButton()
        button.setObjectName("mini")
        button.setText("字段")
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        button.setToolTip("隐藏录屏时不想展示的字段")
        menu = QMenu(button)
        settings = QSettings("BossPilot", "BossPilot")
        saved = settings.value(f"columns/{settings_key}")
        hidden = set(self._settings_texts(saved) if saved is not None else default_hidden)

        for index, caption in enumerate(headers):
            action = QAction(caption, menu)
            action.setCheckable(True)
            action.setChecked(caption not in hidden)
            menu.addAction(action)

            def toggle(
                checked: bool,
                *,
                column: int = index,
                current_action: QAction = action,
            ) -> None:
                visible = [item for item in menu.actions() if item is not current_action and item.isChecked()]
                if not checked and not visible:
                    current_action.setChecked(True)
                    return
                table.setColumnHidden(column, not checked)
                self._save_column_visibility(settings_key, table, headers)

            action.toggled.connect(toggle)
            table.setColumnHidden(index, caption in hidden)

        button.setMenu(menu)
        return button

    def _build_history(self) -> QWidget:
        """沟通记录标签页：直接展示 JSONL 历史，不必再打开文件。"""
        self.history_table = QTableWidget(0, len(self.HISTORY_HEADERS))
        self.history_table.setObjectName("resultsTable")
        self.history_table.setHorizontalHeaderLabels(self.HISTORY_HEADERS)
        self.history_table.setAlternatingRowColors(True)
        self.history_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.history_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.history_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.history_table.setShowGrid(False)
        self.history_table.setWordWrap(False)
        self.history_table.setToolTip("双击任意一行查看岗位详情，点击“打开”跳转岗位页")
        self.history_table.verticalHeader().setVisible(False)
        self.history_table.verticalHeader().setDefaultSectionSize(38)
        history_header = self.history_table.horizontalHeader()
        history_header.setHighlightSections(False)
        history_header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        # 状态列跟随窗口拉伸，避免出现只差 1px 的横向滚动条。
        history_header.setSectionResizeMode(
            self.HISTORY_STATUS_COLUMN, QHeaderView.ResizeMode.Stretch
        )
        history_header.setSectionResizeMode(
            self.HISTORY_OPEN_COLUMN, QHeaderView.ResizeMode.Fixed
        )
        history_header.setStretchLastSection(False)
        for index, width in enumerate((165, 210, 100, 140, 180, 150, 100, 150, 260, 80)):
            self.history_table.setColumnWidth(index, width)

        page = QWidget()
        page.setObjectName("tabPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)
        self.history_count = QLabel("0 条记录")
        self.history_count.setObjectName("resultCount")
        self.history_columns_button = self._column_menu_button(
            self.history_table, self.HISTORY_HEADERS, "history", ()
        )
        refresh = QPushButton("刷新")
        refresh.setObjectName("mini")
        refresh.clicked.connect(self.load_history)
        open_file = QPushButton("打开文件")
        open_file.setObjectName("mini")
        open_file.setToolTip(str(CONTACT_HISTORY_PATH))
        open_file.clicked.connect(self.open_contact_history)
        layout.addLayout(
            self._panel_header(
                "全部历史沟通，最近的排在最前；双击一行查看详情，点“打开”跳转",
                self.history_count,
                self.history_columns_button,
                refresh,
                open_file,
            )
        )
        self.history_stack = QStackedWidget()
        self.history_stack.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.history_stack.addWidget(
            self._empty_state("暂无沟通记录", "开启“自动沟通”后，每次沟通都会写入这里")
        )
        self.history_stack.addWidget(self.history_table)
        layout.addWidget(self.history_stack, 1)
        return page

    def _build_console(self) -> QWidget:
        page = QWidget()
        page.setObjectName("tabPage")
        console_layout = QVBoxLayout(page)
        console_layout.setContentsMargins(1, 1, 1, 1)
        console_layout.setSpacing(0)
        clear_log = QPushButton("清空日志")
        clear_log.setObjectName("mini")
        clear_log.clicked.connect(lambda: self.console.clear())
        console_layout.addLayout(
            self._panel_header("每个岗位的读取字段与筛选原因，最多保留 2000 行", clear_log)
        )

        self.console = QPlainTextEdit()
        self.console.setReadOnly(True)
        self.console.setMaximumBlockCount(2000)
        self.console.setPlaceholderText(
            "开始采集后，这里会显示每个岗位的读取字段和筛选原因。"
        )
        console_layout.addWidget(self.console, 1)
        return page

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
            experiences=self.experience.checked_texts(),
            educations=self.education.checked_texts(),
            max_results=self.max_results.value(),
            max_scrolls=self.max_scrolls.value(),
            auto_contact=self.auto_contact.isChecked(),
            contact_interval=self.contact_interval.value(),
            fetch_detail=self.fetch_detail.isChecked(),
        )
        self.clear_results()
        self.tabs.setCurrentIndex(self.TAB_RESULTS)
        self.worker = JobWorker(options)
        self.worker.job_found.connect(self.add_job)
        self.worker.status_changed.connect(self.search_status_changed)
        self.worker.stats_changed.connect(self.update_stats)
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
        self.append_log("[登录] 已打开 BOSS 登录页。")

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
        self.console.appendPlainText(f"{datetime.now():%H:%M:%S} {message}")

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
        for column, (_, field) in enumerate(self.COLUMNS):
            value = getattr(job, field)
            display = " ".join(value.split()) if column == self.DESCRIPTION_COLUMN else value
            item = QTableWidgetItem(display)
            # 描述是大段文本，tooltip 保留原始换行便于阅读。
            item.setToolTip(value or "")
            if column == 0:
                item.setData(Qt.ItemDataRole.UserRole, job.link)
            if column == self.STATUS_COLUMN:
                item.setForeground(QColor(CONTACT_STATUS_COLORS.get(value, "#b45309")))
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
        self.update_stats(0, 0, 0)
        self._update_results_state()

    @Slot()
    def export_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "导出岗位", "BossPilot_jobs.csv", "CSV 文件 (*.csv)"
        )
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as file:
                writer = csv.writer(file)
                writer.writerow([*self.HEADERS, "详情状态", "链接"])
                for job in self.jobs:
                    writer.writerow(
                        [
                            *(getattr(job, field) for _, field in self.COLUMNS),
                            job.detail_status,
                            job.link,
                        ]
                    )
        except OSError as exc:
            self._set_status(f"导出失败：{exc}", "error")
            QMessageBox.warning(self, "导出失败", str(exc))
            return
        self._set_status(f"已导出 {len(self.jobs)} 条到 {path}", "success")

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
        self._sync_salary_enabled(self.salary_mode.currentText())

    @Slot(str)
    def _sync_salary_enabled(self, mode: str) -> None:
        """薪资规则选择“不限”时，目标月薪输入没有意义。"""
        self.salary.setEnabled(mode != "不限" and self.start_button.isEnabled())

    @staticmethod
    def _save_column_visibility(
        settings_key: str, table: QTableWidget, headers: list[str]
    ) -> None:
        hidden = [
            caption
            for index, caption in enumerate(headers)
            if table.isColumnHidden(index)
        ]
        settings = QSettings("BossPilot", "BossPilot")
        settings.setValue(f"columns/{settings_key}", hidden)
        settings.sync()

    def _update_results_state(self) -> None:
        """根据结果数量切换空状态与岗位表格。"""
        count = len(self.jobs)
        self.result_count.setText(f"{count} 个结果")
        self.results_stack.setCurrentIndex(1 if count else 0)
        self.tabs.setTabText(
            self.TAB_RESULTS, "岗位结果" if not count else f"岗位结果 · {count}"
        )
        self.clear_button.setEnabled(bool(count) and not bool(self.worker))

    def _set_status(self, message: str, state: str = "neutral") -> None:
        """更新状态文案与对应的视觉状态。"""
        self.status.setText(message)
        self.status.setToolTip(message)
        for widget in (self.status_row, self.status_dot):
            widget.setProperty("state", state)
            widget.style().unpolish(widget)
            widget.style().polish(widget)

    @Slot(int, int, int)
    def update_stats(self, scanned: int, matched: int, contacted: int) -> None:
        """刷新状态栏上的读取、匹配与沟通计数。"""
        for key, value in (
            ("scanned", scanned),
            ("matched", matched),
            ("contacted", contacted),
        ):
            self.stat_labels[key].setText(str(value))

    def _filter_chip_values(self) -> dict[str, tuple[str, str]]:
        """每个条件胶囊的 (显示文本, 悬停说明)。"""
        keywords = parse_keywords(self.keywords.text())
        query = self.query.text().strip()
        salary_mode = self.salary_mode.currentText()
        prefix = {"最低薪资不低于": "≥", "薪资范围包含": "含 "}.get(salary_mode, "")
        return {
            "city": (self.city.currentText(), "搜索城市"),
            "query": (query or "未填写", f"BOSS 搜索词：{query or '未填写'}"),
            "keywords": (
                f"{len(keywords)} 个" if keywords else "不限",
                "岗位名称需" + self.keyword_mode.currentText() + "：" + (
                    "、".join(keywords) if keywords else "未填写，不做关键词过滤"
                ),
            ),
            "salary": (
                "不限" if salary_mode == "不限" else f"{prefix}{self.salary.value()}K",
                f"薪资规则：{salary_mode}",
            ),
            "experience": (
                "、".join(self.experience.checked_texts()) or "不限",
                "我的工作经验；岗位要求满足任一所选档位即视为匹配",
            ),
            "education": (
                "、".join(self.education.checked_texts()) or "不限",
                "我的学历；岗位要求满足任一所选档位即视为匹配",
            ),
            "detail": (
                "读取" if self.fetch_detail.isChecked() else "跳过",
                "是否点开卡片读取列表页右侧的职位描述与 HR 信息",
            ),
            "contact": (
                f"每 {self.contact_interval.value()} 秒"
                if self.auto_contact.isChecked()
                else "关闭",
                "匹配后是否立即发起沟通",
            ),
        }

    def _refresh_filter_chips(self, *_args: object) -> None:
        """筛选控件一改动就刷新状态卡里的条件胶囊。"""
        for key, (text, tip) in self._filter_chip_values().items():
            chip, value_label = self.filter_chips[key]
            display = text if len(text) <= 12 else f"{text[:11]}…"
            value_label.setText(display)
            chip.setToolTip(f"{tip}\n当前：{text}" if display != text else tip)

    @Slot(int)
    def _tab_changed(self, index: int) -> None:
        # 沟通记录在采集过程中会不断追加，每次切过来都重新读一遍文件。
        if index == self.TAB_HISTORY:
            self.load_history()
        self.edit_filters_button.setVisible(index != self.TAB_FILTERS)

    @Slot()
    def load_history(self) -> None:
        """读取沟通历史 JSONL；文件缺失或某一行损坏都不影响其余记录。"""
        records: list[dict[str, object]] = []
        try:
            text = CONTACT_HISTORY_PATH.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            text = ""
            if CONTACT_HISTORY_PATH.exists():
                self._set_status(f"读取沟通记录失败：{exc}", "error")
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                records.append(record)
        records.reverse()  # 最近的沟通排在最前

        self.history_table.setRowCount(0)
        self.history_records = records
        for record in records:
            row = self.history_table.rowCount()
            self.history_table.insertRow(row)
            for column, (_, key) in enumerate(self.HISTORY_COLUMNS):
                item = QTableWidgetItem(self._history_cell(record, key))
                item.setToolTip(item.text())
                if key == "open":
                    button = QToolButton()
                    button.setText("打开")
                    button.setObjectName("mini")
                    button.setToolTip(str(record.get("link") or "无岗位链接"))
                    button.setEnabled(bool(record.get("link")))
                    button.clicked.connect(
                        lambda _checked=False, link=str(record.get("link") or ""): self.open_link(link)
                    )
                    self.history_table.setCellWidget(row, column, button)
                    continue
                if column == 0:
                    item.setData(
                        Qt.ItemDataRole.UserRole, str(record.get("link") or "")
                    )
                if column == self.HISTORY_STATUS_COLUMN:
                    item.setForeground(
                        QColor(CONTACT_STATUS_COLORS.get(item.text(), "#b45309"))
                    )
                self.history_table.setItem(row, column, item)
        self.history_count.setText(f"{len(records)} 条记录")
        self.history_stack.setCurrentIndex(1 if records else 0)
        self.tabs.setTabText(
            self.TAB_HISTORY, "沟通记录" if not records else f"沟通记录 · {len(records)}"
        )

    @staticmethod
    def _history_cell(record: dict[str, object], key: str) -> str:
        """把 JSONL 记录里的嵌套字段拍平成一个单元格文本。"""
        if key == "recorded_at":
            return str(record.get("recorded_at") or "")[:19].replace("T", " ")
        if key == "hr":
            hr = record.get("hr")
            hr = hr if isinstance(hr, dict) else {}
            return " · ".join(
                str(hr.get(field) or "") for field in ("name", "title") if hr.get(field)
            )
        if key == "query":
            search = record.get("search")
            search = search if isinstance(search, dict) else {}
            return str(search.get("query") or "")
        if key == "link":
            link = str(record.get("link") or "")
            return link.replace("https://", "").replace("http://", "")
        if key == "open":
            return ""
        return str(record.get(key) or "")

    def open_link(self, link: str) -> None:
        if link:
            QDesktopServices.openUrl(QUrl(link))

    @staticmethod
    def _settings_texts(value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            return (value,) if value else ()
        if isinstance(value, list):
            return tuple(str(item) for item in value if str(item))
        return ()

    def _load_settings(self) -> None:
        settings = QSettings("BossPilot", "BossPilot")
        controls = {
            "city": self.city,
            "keyword_mode": self.keyword_mode,
            "salary_mode": self.salary_mode,
        }
        for key, control in controls.items():
            value = settings.value(f"filters/{key}")
            if value is not None and control.findText(str(value)) >= 0:
                control.setCurrentText(str(value))
        self.experience.set_checked_texts(
            self._settings_texts(settings.value("filters/experiences"))
            or self._settings_texts(settings.value("filters/experience"))
            or ("1-3年",)
        )
        self.education.set_checked_texts(
            self._settings_texts(settings.value("filters/educations"))
            or self._settings_texts(settings.value("filters/education"))
            or ("本科",)
        )

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
        self.fetch_detail.setChecked(
            settings.value("filters/fetch_detail", True, type=bool)
        )
        self.contact_interval.setValue(
            settings.value(
                "filters/contact_interval", self.contact_interval.value(), type=int
            )
        )
        tab = settings.value("window/tab", self.TAB_FILTERS, type=int)
        self.tabs.setCurrentIndex(tab if 0 <= tab < self.tabs.count() else self.TAB_FILTERS)
        # 索引没变化时 currentChanged 不会触发，这里显式同步一次。
        self._tab_changed(self.tabs.currentIndex())
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
            "experiences": list(self.experience.checked_texts()),
            "educations": list(self.education.checked_texts()),
            "max_results": self.max_results.value(),
            "max_scrolls": self.max_scrolls.value(),
            "auto_contact": self.auto_contact.isChecked(),
            "contact_interval": self.contact_interval.value(),
            "fetch_detail": self.fetch_detail.isChecked(),
        }
        for key, value in values.items():
            settings.setValue(f"filters/{key}", value)
        settings.setValue("window/tab", self.tabs.currentIndex())
        settings.setValue("window/geometry", self.saveGeometry())
        settings.sync()

    @Slot(int, int)
    def open_job(self, row: int, column: int) -> None:
        # 双击描述列查看大段文本，双击其他列打开岗位详情页。
        if column == self.DESCRIPTION_COLUMN:
            self.show_job_detail(row)
            return
        item = self.table.item(row, 0)
        link = item.data(Qt.ItemDataRole.UserRole) if item else None
        if link:
            QDesktopServices.openUrl(QUrl(link))

    @Slot(int, int)
    def show_history_detail(self, row: int, _column: int) -> None:
        if 0 <= row < len(self.history_records):
            self.show_job_record_detail(self.history_records[row])

    def show_job_record_detail(self, record: dict[str, object]) -> None:
        self.show_job_detail_for(job_from_history_record(record))

    def show_job_detail(self, row: int) -> None:
        """在弹窗中展示岗位的完整招聘要求与公司、HR 信息。"""
        if not 0 <= row < len(self.jobs):
            return
        self.show_job_detail_for(self.jobs[row])

    def show_job_detail_for(self, job: Job) -> None:
        """在弹窗中展示岗位的完整招聘要求与公司、HR 信息。"""
        company_line = " · ".join(
            part for part in (job.company, job.industry, job.scale, job.stage) if part
        )
        hr_line = " · ".join(
            part for part in (job.hr_name, job.hr_title, job.hr_active) if part
        )
        sections = [
            f"{job.title}　{job.salary}",
            f"{job.area} | {job.experience} | {job.education}",
            f"公司：{company_line or '未读取'}",
            f"HR：{hr_line or '未读取'}",
            f"技能要求：{job.skills or '未读取'}",
            f"福利：{job.benefits or '未读取'}",
            f"工作地址：{job.address or '未读取'}",
            "",
            "职位描述：",
            job.description or f"（{job.detail_status}）",
        ]
        dialog = QMessageBox(self)
        dialog.setWindowTitle("岗位详情")
        dialog.setText(job.title)
        dialog.setInformativeText("\n".join(sections))
        dialog.setStandardButtons(QMessageBox.StandardButton.Close)
        if job.link:
            open_button = dialog.addButton("打开岗位页", QMessageBox.ButtonRole.ActionRole)
            dialog.exec()
            if dialog.clickedButton() is open_button:
                QDesktopServices.openUrl(QUrl(job.link))
            return
        dialog.exec()

    def closeEvent(self, event: object) -> None:
        self._save_settings()
        if self.login_worker and self.login_worker.isRunning():
            self.login_worker.wait(3000)
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait(3000)
        event.accept()


STYLE = """
/* 设计基准：背景 #f4f6f9，卡片 #ffffff，描边 #e2e8f0，主色 #0d7d8c。 */
QWidget { font-size: 13px; color: #1f2a37; }
QWidget#root { background: #f4f6f9; }
QToolTip { padding: 5px 8px; border: 1px solid #cbd5e1; border-radius: 6px;
           background: #1f2a37; color: #f8fafc; }

QLabel#title { font-size: 25px; font-weight: 700; color: #0f1b26; }
QLabel#subtitle { font-size: 12px; color: #6b7a89; }
QLabel#sectionTitle { font-size: 14px; font-weight: 700; color: #1f2a37; }
QLabel#fieldGroup { font-size: 11px; font-weight: 700; color: #8b98a5;
                    letter-spacing: 1px; padding-bottom: 2px; }
QLabel#resultCount { color: #6b7a89; }
QLabel#panelHint { color: #9aa7b4; font-size: 12px; }
QLabel#barCaption { color: #8b98a5; font-size: 11px; font-weight: 700;
                    letter-spacing: 1px; padding-right: 2px; }
QLabel#pageHint { padding: 10px 20px; border-top: 1px solid #eef2f6;
                  background: #f9fbfc; color: #8b98a5; font-size: 12px;
                  border-bottom-left-radius: 10px;
                  border-bottom-right-radius: 10px; }

QTabWidget#mainTabs::pane { border: 0; background: transparent; }
QTabBar { qproperty-drawBase: 0; }
QTabBar::tab { min-height: 24px; padding: 6px 16px; margin: 0 4px 6px 0;
    border: 1px solid transparent; border-radius: 8px; background: transparent;
    color: #6b7a89; font-weight: 600; }
QTabBar::tab:hover { background: #eaf0f5; color: #33414d; }
QTabBar::tab:selected { background: #ffffff; border-color: #dde5ee;
                        color: #0d7d8c; }
QWidget#tabPage { background: #ffffff; border: 1px solid #dde5ee;
                  border-radius: 10px; }
QFrame#vDivider { border: none; border-left: 1px solid #eef2f6;
                  max-width: 1px; margin: 4px 0; }
QFrame#hDivider { border: none; border-top: 1px solid #eef2f6; max-height: 1px; }

QLineEdit, QComboBox, QSpinBox {
    min-height: 31px; padding: 0 10px; border: 1px solid #d6dee8;
    border-radius: 8px; background: #ffffff; selection-background-color: #b8e2e7;
    selection-color: #0f1b26; }
QLineEdit:hover, QComboBox:hover, QSpinBox:hover { border-color: #b6c4d2; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus { border-color: #0d7d8c; }
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {
    background: #f1f4f8; color: #9aa7b4; border-color: #e2e8f0; }
QComboBox::drop-down { width: 22px; border: none; }
QComboBox QAbstractItemView { border: 1px solid #d6dee8; border-radius: 8px;
    background: #ffffff; outline: none; selection-background-color: #e6f3f4;
    selection-color: #0f1b26; padding: 4px; }
QMenu { border: 1px solid #d6dee8; border-radius: 8px; background: #ffffff;
        padding: 5px; }
QMenu::item { padding: 5px 18px 5px 8px; border-radius: 5px; }
QMenu::item:selected { background: #e6f3f4; color: #0f1b26; }
QSpinBox::up-button, QSpinBox::down-button { width: 20px; height: 13px;
    border: none; background: transparent; subcontrol-origin: border; }
QSpinBox::up-button { subcontrol-position: top right; margin: 4px 5px 0 0; }
QSpinBox::down-button { subcontrol-position: bottom right; margin: 0 5px 4px 0; }
QSpinBox::up-button:hover, QSpinBox::down-button:hover { background: #eef3f7;
                                                        border-radius: 3px; }
QCheckBox { spacing: 8px; min-height: 31px; color: #1f2a37; }
QWidget#tabPage QCheckBox { min-height: 26px; color: #5a6b7b; }
QWidget#filterBody QCheckBox { min-height: 31px; color: #1f2a37; }
QCheckBox::indicator { width: 17px; height: 17px; border: 1px solid #c3cedb;
    border-radius: 5px; background: #ffffff; }
QCheckBox::indicator:hover { border-color: #0d7d8c; }
QCheckBox::indicator:checked { border-color: #0d7d8c; background: #0d7d8c; }

QPushButton { min-height: 34px; padding: 0 15px; border: 1px solid #d6dee8;
    border-radius: 8px; background: #ffffff; color: #33414d; font-weight: 500; }
QPushButton:hover { background: #f2f6f9; border-color: #b6c4d2; }
QPushButton:pressed { background: #e7edf3; }
QPushButton:disabled { color: #a5b0bc; background: #f1f4f8; border-color: #e6ebf1; }
QPushButton#primary { color: #ffffff; background: #0d7d8c; border-color: #0d7d8c;
                      font-weight: 600; padding: 0 22px; }
QPushButton#primary:hover { background: #0b6b78; border-color: #0b6b78; }
QPushButton#primary:pressed { background: #095a65; }
QPushButton#primary:disabled { color: #ffffff; background: #a8ccd1;
                               border-color: #a8ccd1; }
QPushButton#danger { color: #b4403f; border-color: #edc9c8; background: #fdf5f5; }
QPushButton#danger:hover { background: #fbe9e8; border-color: #e0a7a6; }
QPushButton#danger:disabled { color: #c0aeae; background: #f5f0f0;
                              border-color: #ece2e2; }
QPushButton#mini, QToolButton#mini { min-height: 26px; padding: 0 11px; font-size: 12px;
                   color: #5a6b7b; }
QPushButton#mini:hover, QToolButton#mini:hover { color: #1f2a37; }

QFrame#statusPanel { border: 1px solid #dde5ee; border-radius: 10px;
                     background: #ffffff; }
QFrame#statusRow { border: 1px solid transparent; border-radius: 7px;
                   background: transparent; }
QFrame#statusRow[state="running"] { border-color: #b9dce1; background: #f2fafb; }
QFrame#statusRow[state="success"] { border-color: #b6ddc9; background: #f3fbf6; }
QFrame#statusRow[state="error"] { border-color: #eec6c5; background: #fdf5f5; }
QLabel#statusDot { border-radius: 4px; background: #a5b0bc; }
QLabel#statusDot[state="running"] { background: #0d7d8c; }
QLabel#statusDot[state="success"] { background: #12805c; }
QLabel#statusDot[state="error"] { background: #c2504e; }
QLabel#status { color: #4b5a68; }
QFrame#statChip { border: 1px solid #e6ebf1; border-radius: 11px;
                  background: #f7f9fc; }
QLabel#statCaption { color: #8b98a5; font-size: 11px; }
QLabel#statValue { color: #1f2a37; font-size: 12px; font-weight: 700; }

QProgressBar { border: 0; border-radius: 3px; background: #dde8ea;
               min-height: 6px; max-height: 6px; }
QProgressBar::chunk { border-radius: 3px; background: #0d7d8c; }

QWidget#emptyState { background: #ffffff; border-bottom-left-radius: 10px;
                     border-bottom-right-radius: 10px; }
QLabel#emptyTitle { color: #5a6b7b; font-size: 15px; font-weight: 600; }
QLabel#emptyHint { color: #9aa7b4; font-size: 12px; }

QTableWidget#resultsTable { border: 0; background: #ffffff;
    alternate-background-color: #fafbfd; gridline-color: transparent;
    outline: none; }
QTableWidget#resultsTable::item { padding: 8px 10px;
    border-bottom: 1px solid #f0f3f7; }
QTableWidget#resultsTable::item:selected { color: #0f1b26; background: #e2f2f4; }
QHeaderView::section { padding: 10px; border: 0; border-bottom: 1px solid #e2e8f0;
    background: #f7f9fc; color: #6b7a89; font-weight: 600; }

QPlainTextEdit { border: 0; border-top: 1px solid #e2e8f0;
    border-bottom-left-radius: 10px; border-bottom-right-radius: 10px;
    background: #16202b; color: #cdd9e4; padding: 10px;
    font-family: "SF Mono", Menlo, Consolas, monospace; font-size: 11px;
    selection-background-color: #2f4b5a; }

QScrollBar:vertical { width: 10px; background: transparent; margin: 0; }
QScrollBar::handle:vertical { min-height: 30px; border-radius: 5px;
                              background: #cdd6e0; }
QScrollBar::handle:vertical:hover { background: #b6c4d2; }
QScrollBar:horizontal { height: 10px; background: transparent; margin: 0; }
QScrollBar::handle:horizontal { min-width: 30px; border-radius: 5px;
                                background: #cdd6e0; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
"""


ARROW_COLORS = {
    "arrow_down": ("#6b7a89", False),
    "arrow_up": ("#6b7a89", True),
    "arrow_down_muted": ("#c3cedb", False),
    "arrow_up_muted": ("#c3cedb", True),
}
# 单独拼接：QSS 的 url() 不接受 data URI，图标必须先落到磁盘。
ARROW_STYLE = """
QComboBox::down-arrow { width: 9px; height: 6px; margin-right: 8px;
                        image: url(@arrow_down@); }
QComboBox::down-arrow:disabled { image: url(@arrow_down_muted@); }
QSpinBox::up-arrow { width: 9px; height: 6px; image: url(@arrow_up@); }
QSpinBox::down-arrow { width: 9px; height: 6px; image: url(@arrow_down@); }
QSpinBox::up-arrow:disabled { image: url(@arrow_up_muted@); }
QSpinBox::down-arrow:disabled { image: url(@arrow_down_muted@); }
"""


def load_logo(height: int) -> QPixmap:
    """按内容裁掉 logo 四周的透明边距，否则在标题栏里显得又小又偏。"""
    image = QImage(str(LOGO_PATH))
    if image.isNull():
        return QPixmap()
    image = image.convertToFormat(QImage.Format.Format_ARGB32)
    step = max(1, image.width() // 128)
    left, top, right, bottom = image.width(), image.height(), 0, 0
    for y in range(0, image.height(), step):
        for x in range(0, image.width(), step):
            if image.pixelColor(x, y).alpha() > 8:
                left, top = min(left, x), min(top, y)
                right, bottom = max(right, x), max(bottom, y)
    if left < right and top < bottom:
        image = image.copy(left, top, right - left + step, bottom - top + step)
    return QPixmap.fromImage(image).scaledToHeight(
        height, Qt.TransformationMode.SmoothTransformation
    )


def build_stylesheet(icon_dir: Path | None = None) -> str:
    """生成完整样式表；箭头图标写盘失败时退回平台原生箭头。"""
    target = icon_dir or Path(QDir.tempPath()) / "bosspilot-icons"
    paths: dict[str, str] = {}
    for name, (color, up) in ARROW_COLORS.items():
        points = "1,5 4.5,1.5 8,5" if up else "1,1 4.5,4.5 8,1"
        svg = (
            "<svg xmlns='http://www.w3.org/2000/svg' width='9' height='6'>"
            f"<polyline points='{points}' fill='none' stroke='{color}'"
            " stroke-width='1.6' stroke-linecap='round'"
            " stroke-linejoin='round'/></svg>"
        )
        try:
            target.mkdir(parents=True, exist_ok=True)
            path = target / f"{name}.svg"
            path.write_text(svg, encoding="utf-8")
        except OSError:
            return STYLE
        paths[name] = path.as_posix()
    arrow_style = ARROW_STYLE
    for name, path_text in paths.items():
        arrow_style = arrow_style.replace(f"@{name}@", path_text)
    return STYLE + arrow_style


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("BossPilot")
    app.setWindowIcon(QIcon(str(LOGO_PATH)))
    app.setStyleSheet(build_stylesheet())
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
