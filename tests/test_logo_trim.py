"""Uploaded club logos get their empty border cropped off. The app fits the
whole logo image into a small header box, so a logo that arrives as a small
mark centred on a big padded square (common — e.g. a 512x512 export) would
otherwise render tiny. Both transparent and plain-white padding count as
empty; the logo's own pixels must survive untouched."""

import base64
import io

from PIL import Image

from app.storage import _trim_logo_margins


def _data_url(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _decode(data_url: str) -> Image.Image:
    return Image.open(io.BytesIO(base64.b64decode(data_url.split(",", 1)[1])))


def _padded_logo(background):
    img = Image.new("RGBA", (512, 512), background)
    img.paste((194, 55, 105, 255), (150, 200, 350, 300))  # 200x100 mark
    return img


def test_transparent_padding_is_cropped_to_the_logo():
    out = _decode(_trim_logo_margins(_data_url(_padded_logo((0, 0, 0, 0)))))
    assert out.size == (200, 100)
    assert out.getpixel((100, 50)) == (194, 55, 105, 255)


def test_white_padding_is_cropped_to_the_logo():
    out = _decode(_trim_logo_margins(_data_url(_padded_logo((255, 255, 255, 255)))))
    assert out.size == (200, 100)


def test_a_logo_with_no_padding_is_left_as_uploaded():
    url = _data_url(Image.new("RGBA", (40, 20), (194, 55, 105, 255)))
    assert _trim_logo_margins(url) == url


def test_bytes_that_are_not_an_image_pass_through_for_the_upload_check():
    url = "data:image/png;base64," + base64.b64encode(b"not an image").decode()
    assert _trim_logo_margins(url) == url
