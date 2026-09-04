from app.domain.text_cleaning import clean_jd_text_for_display


def test_removes_inserted_kanzhun_and_boss_watermark():
    text = "岗位职kanzhun责：\n1、定来自BOSS直聘义AI产品"
    assert clean_jd_text_for_display(text) == "岗位职责:\n1、定义AI产品"


def test_removes_boss_inserted_inside_chinese_word():
    assert clean_jd_text_for_display("负责物流boss算法") == "负责物流算法"
    assert clean_jd_text_for_display("岗位直聘职责") == "岗位职责"


def test_keeps_standalone_legal_boss_terms():
    text = "本司为BOSS直聘，欢迎访问BOSS主页"
    assert clean_jd_text_for_display(text) == "本司为BOSS直聘,欢迎访问BOSS主页"


def test_removes_kanzhun_everywhere_and_nfkc_normalizes():
    text = "查看 kanzhun.com ＡＩ"
    assert clean_jd_text_for_display(text) == "查看 .com AI"


def test_removes_boss_zhipin_phrase_artifact_but_keeps_standalone_brand():
    assert clean_jd_text_for_display("数据来自BOSS直聘官网") == "数据官网"
    assert clean_jd_text_for_display("本公司为BOSS直聘平台") == "本公司为BOSS直聘平台"
