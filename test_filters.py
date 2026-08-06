import main
import json
from dataclasses import replace
from tempfile import TemporaryDirectory
from pathlib import Path
from PySide6.QtWidgets import QApplication
from main import (
    Job,
    JobWorker,
    SearchOptions,
    append_contact_history,
    boss_browser_options,
    decode_boss_text,
    job_from_history_record,
    job_matches,
    hr_rejection_reason,
    refresh_hr_status,
    parse_keywords,
    parse_monthly_salary,
    search_queries,
)


def options(**changes: object) -> SearchOptions:
    values = {
        "city": "杭州",
        "city_code": "101210100",
        "query": "Python",
        "title_keywords": ("全栈", "Python后端"),
        "keyword_mode": "包含任一关键词",
        "salary_k": 12,
        "salary_mode": "最低薪资不低于",
        "experiences": ("3-5年",),
        "educations": ("本科",),
        "max_results": 100,
        "max_scrolls": 30,
        "auto_contact": True,
        "contact_interval": 8,
    }
    values.update(changes)
    return SearchOptions(**values)


def test_salary_parser() -> None:
    assert parse_monthly_salary("12-20K·13薪") == (12, 20)
    assert parse_monthly_salary("200-300元/天") is None
    assert decode_boss_text("\ue033\ue031-\ue035\ue031K") == "20-40K"
    assert parse_monthly_salary(decode_boss_text("\ue033\ue031-\ue035\ue031K")) == (20, 40)
    assert decode_boss_text("1\ue03a-35K") == "19-35K"
    assert parse_monthly_salary("12000-17000元/月") == (12, 17)


def test_salary_parser_units() -> None:
    assert parse_monthly_salary("20K-30K") == (20, 30)
    assert parse_monthly_salary("1.5-2万") == (15, 20)
    assert parse_monthly_salary("8千-1.2万") == (8, 12)
    assert parse_monthly_salary("15K") == (15, 15)
    assert parse_monthly_salary("面议") is None
    assert parse_monthly_salary("13薪") is None
    assert parse_monthly_salary("50万/年") is None
    assert parse_monthly_salary("500-800") is None


def test_keyword_parser() -> None:
    assert parse_keywords("Python, 全栈，Python后端") == (
        "Python",
        "全栈",
        "Python后端",
    )


def test_search_queries_include_title_keywords_once() -> None:
    assert search_queries(
        options(query=" Python ", title_keywords=("python", "全栈", "Python后端"))
    ) == ("Python", "全栈", "Python后端")


def test_job_filters() -> None:
    job = Job("Python后端开发", "12-20K·13薪", "杭州·西湖区", "3-5年", "本科", "示例科技", "Python", "https://example.com")
    assert job_matches(job, options())
    assert not job_matches(job, options(salary_k=15))
    assert job_matches(job, options(salary_k=15, salary_mode="薪资范围包含"))
    assert job_matches(job, options(title_keywords=("PYTHON后端",)))
    assert job_matches(job, options(title_keywords=("python后端",)))


def test_hr_filters_skip_headhunters_and_stale_activity() -> None:
    assert hr_rejection_reason(
        Job("Python后端", hr_title="猎头顾问", hr_kind="猎头", hr_active="刚刚活跃", hr_activity_bucket="最近活跃"),
        options(),
    ) == "HR 身份为猎头：猎头顾问"
    assert hr_rejection_reason(
        Job("Python后端", hr_title="招聘者", hr_kind="招聘方", hr_active="半年前活跃", hr_activity_bucket="较久活跃"),
        options(),
    ) == "HR 活跃状态不够新：半年前活跃"
    assert hr_rejection_reason(
        Job("Python后端", hr_title="招聘者", hr_kind="招聘方", hr_active="近半年活跃", hr_activity_bucket="半年活跃"),
        options(),
    ) == "HR 活跃状态不够新：近半年活跃"
    assert hr_rejection_reason(
        Job("Python后端", hr_title="招聘者", hr_kind="招聘方", hr_active="本周活跃", hr_activity_bucket="最近活跃"),
        options(),
    ) is None


def test_hr_filters_are_configurable() -> None:
    headhunter = Job("Python后端", hr_title="猎头顾问", hr_kind="猎头")
    stale = Job("Python后端", hr_title="招聘者", hr_active="半年前活跃", hr_activity_bucket="较久活跃")
    half_year = Job("Python后端", hr_title="招聘者", hr_active="近半年活跃", hr_activity_bucket="半年活跃")
    assert hr_rejection_reason(headhunter, options(skip_headhunters=False)) is None
    assert hr_rejection_reason(half_year, options(hr_activity_mode="跳过较久活跃")) is None
    assert hr_rejection_reason(stale, options(hr_activity_mode="跳过较久活跃")) == "HR 活跃状态过旧：半年前活跃"
    assert hr_rejection_reason(stale, options(hr_activity_mode="不限")) is None


def test_detail_hr_activity_recomputes_unknown_bucket() -> None:
    job = refresh_hr_status(
        Job(
            "Python后端",
            hr_title="招聘者",
            hr_active="半年前活跃",
            hr_kind="招聘方",
            hr_activity_bucket="未知",
        )
    )
    assert job.hr_activity_bucket == "较久活跃"
    assert hr_rejection_reason(job, options()) == "HR 活跃状态不够新：半年前活跃"


def test_unlimited_requirements_are_accepted() -> None:
    job = Job(
        "Python后端开发",
        "12-20K",
        "杭州·西湖区",
        "经验不限",
        "学历不限",
        "示例科技",
        "Python",
        "https://example.com",
    )
    assert job_matches(job, options())


def test_lower_requirements_are_accepted() -> None:
    """岗位要求低于所选档位时也应通过（本科可投大专岗）。"""
    job = Job(
        "Python后端开发",
        "12-20K",
        "杭州·西湖区",
        "1-3年",
        "大专",
        "示例科技",
        "Python",
        "https://example.com",
    )
    assert job_matches(job, options())
    assert not job_matches(job, options(experiences=("1年以内",), educations=("大专",)))
    assert not job_matches(job, options(educations=("高中",)))


def test_multiple_requirement_choices_are_or_matched() -> None:
    job = Job(
        "Python后端开发",
        "12-20K",
        "杭州·西湖区",
        "3-5年",
        "大专",
        "示例科技",
        "Python",
        "https://example.com",
    )
    assert job_matches(job, options(experiences=("1-3年", "3-5年"), educations=("大专", "本科")))
    assert not job_matches(job, options(experiences=("1年以内", "1-3年"), educations=("本科",)))


def test_contact_actions() -> None:
    class Button:
        def __init__(self, text: str) -> None:
            self.text = text
            self.clicked = False

        def click(self) -> None:
            self.clicked = True

    class Page:
        def __init__(
            self, selected: bool, button: Button, stay_button: Button | None = None
        ) -> None:
            self.selected = selected
            self.button = button
            self.stay_button = stay_button

        def run_js(self, *_args: object) -> bool:
            return self.selected

        def ele(self, selector: str, **_kwargs: object) -> Button | None:
            if "greet-boss-container" in selector:
                return self.stay_button
            return self.button

    job = Job("Python后端", "12-20K", "杭州", "3-5年", "本科", "示例科技", "", "https://example.com/job")
    main.time.sleep = lambda _seconds: None
    new_button = Button("立即沟通")
    stay_button = Button("留在此页")
    assert JobWorker._contact(Page(True, new_button, stay_button), job) == "已发起沟通"
    assert new_button.clicked
    assert stay_button.clicked
    no_stay_button = Button("立即沟通")
    assert JobWorker._contact(Page(True, no_stay_button), job) == "未完成（未出现沟通结果弹窗）"
    assert no_stay_button.clicked
    old_button = Button("继续沟通")
    assert JobWorker._contact(Page(True, old_button), job) == "此前已沟通"
    assert not old_button.clicked
    assert JobWorker._contact(Page(False, new_button), job) == "未找到岗位卡片"


def test_login_required_detection() -> None:
    class Page:
        def __init__(self, value: bool) -> None:
            self.value = value

        def run_js(self, _script: str) -> bool:
            return self.value

    assert JobWorker._login_required(Page(True))
    assert not JobWorker._login_required(Page(False))


def test_cards_then_login_prompt_stops_before_collecting() -> None:
    class Page:
        checks = 0

        def eles(self, *_args: object, **_kwargs: object) -> list[object]:
            return [object()]

        def run_js(self, _script: str) -> bool:
            self.checks += 1
            return self.checks > 1

    worker = JobWorker(options())
    main.time.sleep = lambda _seconds: None
    page = Page()

    assert worker._wait_for_cards(page)
    assert not worker._login_required(page)
    assert worker._login_required(page)


def test_read_detail() -> None:
    """详情面板标题与岗位一致时读取字段，并从 HR 文本补出职位。"""

    class Page:
        def __init__(self, detail: dict[str, str] | None) -> None:
            self.detail = detail

        def run_js(self, script: str, *args: object) -> object:
            # 第一次调用是点击卡片的脚本，带 link 参数。
            return True if args else self.detail

    job = Job("python 工程师", "14-28K", "杭州", "经验不限", "本科", "橙风信息", "", "https://e.com/1")
    worker = JobWorker(options())
    main.time.sleep = lambda _seconds: None

    detail = worker._read_detail(
        Page(
            {
                "title": "python 工程师",
                "skills": "PyTorch / Docker / MySQL",
                "description": "1. 熟练掌握Python语言。\n2. 了解大模型优先。",
                "benefits": "五险一金 / 加班补助",
                "address": "杭州萧山区天人大厦33",
                "hr_name": "",
                "hr_text": "刘晨 刚刚活跃\n杭州橙风信息科技 · hr",
                "hr_active": "刚刚活跃",
            }
        ),
        job,
    )
    assert detail["detail_status"] == "已读取"
    assert detail["skills"] == "PyTorch / Docker / MySQL"
    assert detail["address"] == "杭州萧山区天人大厦33"
    assert detail["hr_name"] == "刘晨 刚刚活跃"
    assert detail["hr_title"] == "hr"
    assert detail["hr_active"] == "刚刚活跃"
    assert "了解大模型优先" in detail["description"]
    assert "hr_text" not in detail

    assert worker._read_detail(Page(None), job)["detail_status"] == "未出现详情面板"
    no_desc = worker._read_detail(Page({"title": "python 工程师", "description": ""}), job)
    assert no_desc["detail_status"] == "无职位描述"


def test_build_job_ignores_unknown_fields() -> None:
    job = main.build_job(
        {
            "title": "Python后端",
            "salary": "-K",
            "scale": "100-499人",
            "unexpected": "忽略",
        }
    )
    assert job.salary == "12-20K"
    assert job.scale == "100-499人"
    assert job.detail_status == "未读取"


def test_history_record_keeps_detail_description() -> None:
    job = job_from_history_record(
        {
            "title": "Python后端",
            "salary": "20-40K",
            "link": "https://example.com/job",
            "company_profile": {"industry": "AI", "scale": "100-499人"},
            "hr": {"name": "沈盼盼", "title": "招聘者"},
            "detail": {
                "status": "已读取",
                "skills": "Python / MySQL",
                "description": "岗位职责：写 Python。",
                "address": "杭州钱塘区",
            },
        }
    )
    assert job.description == "岗位职责：写 Python。"
    assert job.detail_status == "已读取"
    assert job.address == "杭州钱塘区"
    assert job.hr_name == "沈盼盼"


def test_history_record_keeps_hr_status_fields() -> None:
    job = job_from_history_record(
        {
            "title": "Python后端",
            "hr": {
                "name": "沈盼盼",
                "title": "招聘者",
                "active": "本周活跃",
                "kind": "招聘方",
                "activity_bucket": "最近活跃",
            },
        }
    )
    assert job.hr_kind == "招聘方"
    assert job.hr_activity_bucket == "最近活跃"


def test_browser_options_reuses_only_live_saved_port() -> None:
    with TemporaryDirectory() as directory:
        path = Path(directory) / "browser_port.txt"
        path.write_text("9222", encoding="utf-8")

        first = boss_browser_options(
            path,
            port_factory=lambda: 9333,
            alive_check=lambda port: port == 9222,
        )
        assert first.address == "127.0.0.1:9222"
        assert path.read_text(encoding="utf-8") == "9222"

        second = boss_browser_options(
            path,
            port_factory=lambda: 9444,
            alive_check=lambda _port: False,
        )
        assert second.address == "127.0.0.1:9444"
        assert path.read_text(encoding="utf-8") == "9444"


def test_contact_history() -> None:
    job = Job("Python后端", "12-20K", "杭州", "3-5年", "本科", "示例科技", "Python", "https://example.com/job")
    with TemporaryDirectory() as directory:
        path = Path(directory) / "history.jsonl"
        append_contact_history(replace(job, contact_status="已发起沟通"), options(), path)
        append_contact_history(replace(job, contact_status="此前已沟通"), options(), path)
        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        assert len(records) == 2
        assert records[0]["contacted_at"]
        assert records[1]["contacted_at"] is None
        assert records[0]["title"] == "Python后端"
        assert records[0]["search"]["title_keywords"] == ["全栈", "Python后端"]


def test_main_window_initializes() -> None:
    app = QApplication.instance() or QApplication([])
    window = main.MainWindow()
    assert window.table.columnCount() == len(window.HEADERS)
    window.close()
