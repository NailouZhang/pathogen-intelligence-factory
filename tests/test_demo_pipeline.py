from pathlib import Path

from src.pifactory.config import Settings
from src.pifactory.pipeline import run_pipeline


def test_demo_pipeline(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    settings = Settings("hantavirus", root, tmp_path, tmp_path / "data/state")
    issue = run_pipeline(settings, demo=True)
    assert issue["metrics"]["papers"] == 2
    assert (tmp_path / "data/latest.json").exists()
    assert (tmp_path / "site/index.html").exists()
    html = (tmp_path / "site/index.html").read_text(encoding="utf-8")
    assert 'class="language-toggle"' in html
    assert ">en</button>" in html
    assert "研究对371名林业工作者开展汉坦病毒抗体检测" in html
    assert "本期文献进展" in html
    assert "本期新闻动态" in html
    assert "查看研究七要素" in html
    assert "查看综述五要素" in html
    assert "翻译暂不可用" not in html
