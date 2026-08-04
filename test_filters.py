import main
import json
from dataclasses import replace
from tempfile import TemporaryDirectory
from pathlib import Path
from main import (
    Job,
    JobWorker,
    SearchOptions,
    append_contact_history,
    decode_boss_text,
    job_matches,
    parse_keywords,
    parse_monthly_salary,
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
        "experience": "3-5年",
        "education": "本科",
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


def test_keyword_parser() -> None:
    assert parse_keywords("Python, 全栈，Python后端") == (
        "Python",
        "全栈",
        "Python后端",
    )


def test_job_filters() -> None:
    job = Job("Python后端开发", "12-20K·13薪", "杭州·西湖区", "3-5年", "本科", "示例科技", "Python", "https://example.com")
    assert job_matches(job, options())
    assert not job_matches(job, options(salary_k=15))
    assert job_matches(job, options(salary_k=15, salary_mode="薪资范围包含"))
    assert job_matches(job, options(title_keywords=("PYTHON后端",)))
    assert job_matches(job, options(title_keywords=("python后端",)))


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
