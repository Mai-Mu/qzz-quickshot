# Third-party notices

The Windows release contains unmodified third-party binaries and models. License texts are in the adjacent `licenses` directory.

## Application runtime

| Component | License and source |
| --- | --- |
| Python 3.12 | Python Software Foundation license. [Source](https://www.python.org/downloads/source/) |
| Qt 6.12 and Qt for Python 6.12 | LGPL 3.0 option for the bundled Qt Essentials and Shiboken libraries. Qt libraries remain external files in `_internal` and can be replaced with compatible builds. [Qt source](https://download.qt.io/official_releases/qt/6.12/6.12.0/single/) · [Qt for Python source](https://code.qt.io/cgit/pyside/pyside-setup.git/tag/?h=v6.12.0) |
| ZXing-C++ 3.1.1 | Apache License 2.0. [Source](https://github.com/zxing-cpp/zxing-cpp/tree/v3.1.1) |
| PyInstaller 6.22.3 bootloader | GPL with the PyInstaller bootloader exception. [Source](https://github.com/pyinstaller/pyinstaller/tree/v6.22.3) |

## OCR runtime

The OCR executable and dynamically loaded libraries are from the unmodified Tesseract 5.5.3 upstream Windows release. The English and Simplified Chinese models come from the `tessdata_fast` 4.1.0 release. Both use the Apache License 2.0. [Tesseract release](https://github.com/tesseract-ocr/tesseract/releases/tag/5.5.3) · [Model release](https://github.com/tesseract-ocr/tessdata_fast/releases/tag/4.1.0)

The OCR libraries listed below are included as separate DLLs in `_internal/ocr_runtime`. The linked sources contain component-specific copyright and license information.

- [libarchive](https://github.com/libarchive/libarchive/tree/v3.8.8), [curl](https://github.com/curl/curl/tree/curl-8_21_0), [Leptonica](https://github.com/DanBloomberg/leptonica/tree/1.87.0), [LibTIFF](https://gitlab.com/libtiff/libtiff/-/tree/v4.7.2)
- [GCC runtime libraries](https://gcc.gnu.org/releases.html), [MinGW-w64 winpthreads](https://github.com/mingw-w64/mingw-w64/tree/master/mingw-w64-libraries/winpthreads)
- [libb2](https://github.com/BLAKE2/libb2), [bzip2](https://sourceware.org/bzip2/), [Expat](https://github.com/libexpat/libexpat/tree/R_2_8_2), [GNU libiconv](https://www.gnu.org/software/libiconv/), [GNU gettext / libintl](https://www.gnu.org/software/gettext/)
- [LZ4](https://github.com/lz4/lz4/tree/v1.10.0), [XZ Utils / liblzma](https://github.com/tukaani-project/xz/tree/v5.8.3), [zlib](https://github.com/madler/zlib/tree/v1.3.2), [Zstandard](https://github.com/facebook/zstd/tree/v1.5.7), [Brotli](https://github.com/google/brotli/tree/v1.2.0)
- [GNU libidn2](https://www.gnu.org/software/libidn/#libidn2), [libpsl](https://github.com/rockdaboot/libpsl/tree/0.21.5), [libssh2](https://github.com/libssh2/libssh2/tree/libssh2-1.11.1), [GNU libunistring](https://www.gnu.org/software/libunistring/)
- [GIFLIB](https://sourceforge.net/projects/giflib/), [libjpeg-turbo](https://github.com/libjpeg-turbo/libjpeg-turbo/tree/3.1.3), [OpenJPEG](https://github.com/uclouvain/openjpeg/tree/v2.5.4), [libpng](https://github.com/pnggroup/libpng/tree/v1.6.58)
- [libwebp and SharpYUV](https://github.com/webmproject/libwebp/tree/v1.6.0), [libdeflate](https://github.com/ebiggers/libdeflate), [JBIG-KIT](https://www.cl.cam.ac.uk/~mgk25/jbigkit/), [Lerc](https://github.com/Esri/lerc)
