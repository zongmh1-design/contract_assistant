"""生成可公开演示的虚构中文文本型 PDF。"""

from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas


OUTPUT_PATH = Path(__file__).with_name("demo_procurement_contract.pdf")
FONT_CANDIDATES = (
    Path("C:/Windows/Fonts/msyh.ttc"),
    Path("C:/Windows/Fonts/simhei.ttf"),
)


def _font_path() -> Path:
    for candidate in FONT_CANDIDATES:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("未找到用于生成中文演示合同的字体")


def _draw_page(pdf: canvas.Canvas, title: str, lines: list[str], page: int) -> None:
    pdf.setFont("DemoChinese", 18)
    pdf.drawString(64, 790, title)
    pdf.setFont("DemoChinese", 12)
    y = 748
    for line in lines:
        pdf.drawString(64, y, line)
        y -= 28
    pdf.setFont("DemoChinese", 9)
    pdf.drawRightString(530, 36, f"第 {page} 页")
    pdf.showPage()


def main() -> None:
    pdfmetrics.registerFont(TTFont("DemoChinese", str(_font_path()), subfontIndex=0))
    pdf = canvas.Canvas(str(OUTPUT_PATH), pagesize=A4, invariant=True)
    _draw_page(
        pdf,
        "设备采购合同",
        [
            "合同编号：DEMO-2026-001",
            "甲方：星海创新科技有限公司（虚构）",
            "乙方：远山设备供应有限公司（虚构）",
            "合同总金额为人民币500000元整",
            "生效日期：2026年10月1日",
            "到期日期：2027年9月30日",
        ],
        1,
    )
    _draw_page(
        pdf,
        "付款条款",
        [
            "付款条款：合同生效后五个工作日内支付预付款，预付款比例为70%。",
            "剩余款项在验收合格并收到发票后30天内支付。",
        ],
        2,
    )
    _draw_page(
        pdf,
        "验收条款",
        [
            "验收条款：设备到货后十个工作日内按技术清单完成验收。",
            "验收不合格的，乙方应在五个工作日内完成整改。",
        ],
        3,
    )
    _draw_page(
        pdf,
        "争议解决条款",
        [
            "争议解决条款：协商不成的，提交甲方所在地人民法院管辖。",
            "本演示合同仅用于软件功能展示。",
        ],
        4,
    )
    pdf.save()
    print(OUTPUT_PATH)


if __name__ == "__main__":
    main()
