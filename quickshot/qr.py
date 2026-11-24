"""Decode one QR code from a local screenshot."""

from PySide6.QtGui import QImage


def decode_qr(image: QImage) -> str:
    if image.isNull():
        return ""

    import zxingcpp

    grayscale = image.convertToFormat(QImage.Format.Format_Grayscale8)
    pixels = zxingcpp.ImageView(
        grayscale.constBits(), grayscale.width(), grayscale.height(),
        zxingcpp.ImageFormat.Lum, grayscale.bytesPerLine(), 1,
    )
    barcode = zxingcpp.read_barcode(
        pixels,
        formats=zxingcpp.BarcodeFormat.QRCode,
        text_mode=zxingcpp.TextMode.Plain,
    )
    return barcode.text if barcode is not None and barcode.valid else ""
