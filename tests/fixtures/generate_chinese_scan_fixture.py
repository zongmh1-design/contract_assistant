"""生成不含文字层的虚构中文合同扫描 PDF。"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas


FIXTURE_DIR = Path(__file__).parent
FONT_CANDIDATES = (
    Path("C:/Windows/Fonts/msyh.ttc"),
    Path("C:/Windows/Fonts/simhei.ttf"),
)


def find_chinese_font() -> Path:
    for candidate in FONT_CANDIDATES:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("未找到用于生成中文 OCR 夹具的字体")


def main() -> None:
    lines = [
        "软件采购合同",
        "合同编号：HT-2026-0098",
        "甲方：星河科技有限公司（虚构）",
        "乙方：远航软件服务有限公司（虚构）",
        "合同总金额为人民币500000元整",
        "付款条款：合同生效后十个工作日内支付首期款",
    ]
    image = Image.new("RGB", (1654, 2339), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(str(find_chinese_font()), 58)
    for index, line in enumerate(lines):
        draw.text((130, 180 + index * 170), line, fill="black", font=font)

    output_path = FIXTURE_DIR / "chinese_contract_scan.pdf"
    pdf = canvas.Canvas(str(output_path), pagesize=A4, invariant=True)
    pdf.drawImage(ImageReader(image), 0, 0, width=A4[0], height=A4[1])
    pdf.showPage()
    pdf.save()


if __name__ == "__main__":
    main()
