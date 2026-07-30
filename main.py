from DrissionPage import ChromiumPage


def main() -> None:
    page = ChromiumPage()
    page.get("https://www.zhipin.com/")
    print(page.title)


if __name__ == "__main__":
    main()
