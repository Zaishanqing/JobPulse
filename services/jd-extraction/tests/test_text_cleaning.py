from src.text_cleaning import clean_jd_text


def test_removes_inserted_kanzhun_and_boss_watermark():
    assert clean_jd_text("岗位职kanzhun责：\n1、定来自BOSS直聘义AI产品") == (
        "岗位职责:\n1、定义AI产品"
    )


def test_removes_boss_inserted_inside_chinese_word():
    assert clean_jd_text("负责物流boss算法") == "负责物流算法"
    assert clean_jd_text("岗位直聘职责") == "岗位职责"


def test_removes_full_boss_token_inserted_inside_words():
    assert clean_jd_text("岗位职BOSS直聘责：精通Java面向对象BOSS直聘编程") == (
        "岗位职责:精通Java面向对象编程"
    )
    assert clean_jd_text("希望BOSS直聘你负责客户BOSS直聘开拓") == (
        "希望你负责客户开拓"
    )
    assert clean_jd_text("javaBOSS直聘开发,有数据分析经验") == "java开发,有数据分析经验"


def test_keeps_standalone_legal_boss_terms():
    assert clean_jd_text("本司为BOSS直聘，欢迎访问BOSS主页") == "本司为BOSS直聘,欢迎访问BOSS主页"
    assert clean_jd_text("50-80K×16薪\nBOSS直聘\n北京\n朝阳区") == (
        "50-80K×16薪\nBOSS直聘\n北京\n朝阳区"
    )


def test_removes_kanzhun_everywhere():
    assert clean_jd_text("查看 kanzhun.com ＡＩ") == "查看 .com AI"
