from __future__ import annotations

from pifactory.news_quality import diagnose_news_text

NOISY = [
    "该网站在您的计算机上存储cookie。这些 cookie 用于改进您的网站。接受 疾病与疾病 慢性肺部疾病 传染病 重症监护 心肺与胸部 睡眠医学 公共卫生 医疗保健与政策 儿科 治疗产品 诊断与测试 制药 行业与监管新闻 部门管理 临床教育 会议 CEU 资源 视频 网络研讨会 白皮书 查看全部 " * 8,
    "跳到内容 菜单 医疗主页 生命科学主页 成为会员 搜索 关于 功能性食品 新闻 健康 A-Z 药物 医疗器械 访谈 白皮书 更多 热门健康类别 冠状病毒病 COVID-19 饮食与营养 人工智能 过敏 阿尔茨海默病 关节炎 乳腺癌 糖尿病 心脏病 肺癌 心理健康 怀孕 睡眠 泌尿科 查看全部 " * 8,
    "在我们的应用程序中打开 获得最佳体验 了解更多 在浏览器中继续 切换导航 所有行政区 最新新闻 头条新闻 公共安全 教育 天气 交互式雷达 政治 艺术文化 社区 邻里 播客 登录 注销 最近活动 " * 12,
    "食品、益处和健康影响 腌肉硝酸盐和癌症风险 非洲传统饮食 白皮书 查看全部 为您的工作流程选择最佳酶标仪 生命科学文章 查看全部 装置/设备 查看全部 DryCal 气体流量校准器 最近的 MediKnowledge 查看全部 加速药物发现和开发有效的数据管理 " * 10,
    "今日建议 世界印第克邮报 支持我们的使命 变革的代理人 印度空间研究组织 教育家 国际女性企业家奖 变革对话 政府 外交 编辑观点 内部挑战 文化遗产 国防安全 发展 经济 健康 科学 教育 体育 印度 查看全部 新闻行动智库 新闻社会企业 " * 10,
]


def test_user_reported_navigation_matrices_are_rejected() -> None:
    for text in NOISY:
        result = diagnose_news_text(text)
        assert result["boilerplate_contaminated"] is True
        assert result["rejection_reasons"]


def test_clean_article_and_clean_summary_use_different_acceptance_surfaces() -> None:
    article = "\n".join([
        "The regional health authority confirmed a hantavirus infection in a rural resident.",
        "The patient was hospitalized while laboratory testing and exposure investigation continued.",
        "Officials advised residents to ventilate buildings and avoid contact with rodent droppings.",
        "No secondary cases were confirmed, and the source of exposure remained under investigation.",
    ])
    assert diagnose_news_text(article)["boilerplate_contaminated"] is False
    summary = "The authority confirmed hantavirus infection. The patient was hospitalized and exposure investigation continued."
    assert diagnose_news_text(summary, content_kind="summary")["boilerplate_contaminated"] is False
