"""Copy license texts and component attributions into the Windows bundle."""

from concurrent.futures import ThreadPoolExecutor
from importlib.metadata import distribution
from pathlib import Path
import shutil
import sys
from urllib.request import urlopen


PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_GITHUB = "https://raw.githubusercontent.com"
LICENSE_URLS = {
    "Qt-LGPL-3.0.txt": f"{RAW_GITHUB}/qt/qtbase/v6.12.0/LICENSES/LGPL-3.0-only.txt",
    "GNU-GPL-3.0.txt": f"{RAW_GITHUB}/qt/qtbase/v6.12.0/LICENSES/GPL-3.0-only.txt",
    "GNU-LGPL-2.1.txt": f"{RAW_GITHUB}/spdx/license-list-data/main/text/LGPL-2.1-only.txt",
    "GNU-GPL-2.0.txt": f"{RAW_GITHUB}/spdx/license-list-data/main/text/GPL-2.0-only.txt",
    "GCC-Runtime-Exception.txt": f"{RAW_GITHUB}/gcc-mirror/gcc/master/COPYING.RUNTIME",
    "winpthreads.txt": f"{RAW_GITHUB}/mingw-w64/mingw-w64/master/mingw-w64-libraries/winpthreads/COPYING",
    "libarchive.txt": f"{RAW_GITHUB}/libarchive/libarchive/v3.8.8/COPYING",
    "curl.txt": f"{RAW_GITHUB}/curl/curl/curl-8_21_0/COPYING",
    "Leptonica.txt": f"{RAW_GITHUB}/DanBloomberg/leptonica/1.87.0/leptonica-license.txt",
    "libtiff.txt": f"{RAW_GITHUB}/libsdl-org/libtiff/v4.7.2/LICENSE.md",
    "libb2.txt": f"{RAW_GITHUB}/BLAKE2/libb2/master/COPYING",
    "bzip2.txt": "https://sourceware.org/git/?p=bzip2.git;a=blob_plain;f=LICENSE;hb=refs/tags/bzip2-1.0.8",
    "Expat.txt": f"{RAW_GITHUB}/libexpat/libexpat/R_2_8_2/expat/COPYING",
    "LZ4.txt": f"{RAW_GITHUB}/lz4/lz4/v1.10.0/lib/LICENSE",
    "XZ-Utils.txt": f"{RAW_GITHUB}/tukaani-project/xz/v5.8.3/COPYING",
    "zlib.txt": f"{RAW_GITHUB}/madler/zlib/v1.3.2/LICENSE",
    "Zstandard.txt": f"{RAW_GITHUB}/facebook/zstd/v1.5.7/LICENSE",
    "Brotli.txt": f"{RAW_GITHUB}/google/brotli/v1.2.0/LICENSE",
    "libpsl.txt": f"{RAW_GITHUB}/rockdaboot/libpsl/0.21.5/LICENSE",
    "libssh2.txt": f"{RAW_GITHUB}/libssh2/libssh2/libssh2-1.11.1/COPYING",
    "giflib.txt": "https://sourceforge.net/p/giflib/code/ci/master/tree/COPYING?format=raw",
    "libjpeg-turbo.txt": f"{RAW_GITHUB}/libjpeg-turbo/libjpeg-turbo/3.1.3/LICENSE.md",
    "libjpeg-ijg.txt": f"{RAW_GITHUB}/libjpeg-turbo/libjpeg-turbo/3.1.3/README.ijg",
    "OpenJPEG.txt": f"{RAW_GITHUB}/uclouvain/openjpeg/v2.5.4/LICENSE",
    "libpng.txt": f"{RAW_GITHUB}/pnggroup/libpng/v1.6.58/LICENSE",
    "libwebp.txt": f"{RAW_GITHUB}/webmproject/libwebp/v1.6.0/COPYING",
    "libwebp-PATENTS.txt": f"{RAW_GITHUB}/webmproject/libwebp/v1.6.0/PATENTS",
    "libdeflate.txt": f"{RAW_GITHUB}/ebiggers/libdeflate/master/COPYING",
    "Lerc.txt": f"{RAW_GITHUB}/Esri/lerc/master/LICENSE",
}


def fetch_license(item: tuple[str, str], license_dir: Path, cache: Path) -> None:
    filename, url = item
    cached_file = cache / filename
    if not cached_file.is_file():
        with urlopen(url, timeout=30) as response:
            content = response.read()
        if not content.strip() or content.lstrip().lower().startswith(b"<!doctype html"):
            raise ValueError(f"Unexpected license response: {url}")
        cached_file.write_bytes(content)
    shutil.copyfile(cached_file, license_dir / filename)


def collect_notices(bundle: Path) -> None:
    license_dir = bundle / "licenses"
    license_dir.mkdir(parents=True, exist_ok=True)
    license_cache = PROJECT_ROOT / "build" / "downloads" / "licenses"
    license_cache.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=8) as workers:
        pending = [
            workers.submit(fetch_license, item, license_dir, license_cache)
            for item in LICENSE_URLS.items()
        ]
        for future in pending:
            future.result()

    shutil.copyfile(Path(sys.base_prefix) / "LICENSE.txt", license_dir / "Python.txt")
    for package in ("PyInstaller", "zxing-cpp"):
        metadata = distribution(package)
        for file in metadata.files or ():
            if "licenses" in file.parts:
                shutil.copyfile(metadata.locate_file(file), license_dir / f"{package}-{file.name}")

    for license_file in (PROJECT_ROOT / "ocr_runtime" / "licenses").iterdir():
        shutil.copyfile(license_file, license_dir / license_file.name)
    shutil.copyfile(PROJECT_ROOT / "THIRD_PARTY_NOTICES.md", bundle / "THIRD_PARTY_NOTICES.md")


if __name__ == "__main__":
    collect_notices(Path(sys.argv[1]))
