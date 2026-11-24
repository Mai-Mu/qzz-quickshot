[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$projectRoot = Split-Path -Parent $PSScriptRoot
$downloadRoot = Join-Path $projectRoot 'build/downloads'
$runtimeRoot = Join-Path $projectRoot 'ocr_runtime'
$modelRoot = Join-Path $runtimeRoot 'tessdata'
New-Item -ItemType Directory -Path $downloadRoot, $modelRoot -Force | Out-Null

function Get-VerifiedDownload {
    param([string]$Uri, [string]$Name, [string]$Sha256)

    $destination = Join-Path $downloadRoot $Name
    if ((Test-Path -LiteralPath $destination -PathType Leaf) -and
        ((Get-FileHash -LiteralPath $destination).Hash -eq $Sha256)) {
        return $destination
    }

    Write-Host "Downloading $Name"
    $temporary = Join-Path $downloadRoot "$Name.$([guid]::NewGuid().ToString('N')).part"
    try {
        Invoke-WebRequest -Uri $Uri -OutFile $temporary -UseBasicParsing
        if ((Get-FileHash -LiteralPath $temporary).Hash -ne $Sha256) {
            throw "The SHA-256 digest for $Name does not match."
        }
        Move-Item -LiteralPath $temporary -Destination $destination -Force
    } finally {
        if (Test-Path -LiteralPath $temporary) {
            Remove-Item -LiteralPath $temporary
        }
    }
    return $destination
}

# The Tesseract installer digest matches the upstream release in the WinGet manifest.
$tesseractName = 'tesseract-ocr-w64-setup-5.5.3.20260724.exe'
$tesseractBase = 'https://github.com/tesseract-ocr/tesseract/releases/download/5.5.3'
$installerDigest = 'BEE9E3434BD94FD65387D9BE28CD467A41F61B1275383B55B0F59A1331270AE4'
$installer = Get-VerifiedDownload "$tesseractBase/$tesseractName" $tesseractName $installerDigest

$modelBase = 'https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/4.1.0'
$englishModel = Get-VerifiedDownload "$modelBase/eng.traineddata" 'eng.traineddata' '7D4322BD2A7749724879683FC3912CB542F19906C83BCC1A52132556427170B2'
$chineseModel = Get-VerifiedDownload "$modelBase/chi_sim.traineddata" 'chi_sim.traineddata' 'A5FCB6F0DB1E1D6D8522F39DB4E848F05984669172E584E8D76B6B3141E1F730'
$modelLicense = Get-VerifiedDownload "$modelBase/LICENSE" 'LICENSE' 'CFC7749B96F63BD31C3C42B5C471BF756814053E847C10F3EB003417BC523D30'

$extractRoot = Join-Path $downloadRoot 'tesseract-extract'
$extractionMarker = Join-Path $extractRoot '.source-sha256'
if (-not (Test-Path -LiteralPath $extractionMarker -PathType Leaf) -or
    (Get-Content -LiteralPath $extractionMarker) -ne $installerDigest) {
    $extractorBase = 'https://github.com/ip7z/7zip/releases/download/26.05'
    $standaloneExtractor = Get-VerifiedDownload "$extractorBase/7zr.exe" '7zr.exe' '9BF582B894DFA665F852D7E9112B02290A1C303C2D95A21FCDC41FFCB03143B7'
    $extractorArchive = Get-VerifiedDownload "$extractorBase/7z2605-x64.exe" '7z2605-x64.exe' 'FD99E86F57D8F429EF5ED045BF98315AF962E2309B0F1BA87A147A44C71647D0'
    $extractorRoot = Join-Path $downloadRoot '7zip'
    & $standaloneExtractor x $extractorArchive "-o$extractorRoot" -y | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw 'Could not unpack the 7-Zip extractor.'
    }
    $sevenZip = Join-Path $extractorRoot '7z.exe'
    & $sevenZip x $installer "-o$extractRoot" -y | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw 'Could not unpack the Tesseract OCR installer.'
    }
    Set-Content -LiteralPath $extractionMarker -Value $installerDigest -Encoding ascii
}

# Transitive Windows imports of the OCR executable, excluding Windows system DLLs.
$ocrFiles = @(
    'tesseract.exe', 'libtesseract-5.dll', 'libarchive-13.dll', 'libcurl-4.dll',
    'libleptonica-6.dll', 'libtiff-6.dll', 'libgcc_s_seh-1.dll', 'libstdc++-6.dll',
    'libwinpthread-1.dll', 'libb2-1.dll', 'libbz2-1.dll', 'libexpat-1.dll',
    'libiconv-2.dll', 'liblz4.dll', 'liblzma-5.dll', 'zlib1.dll', 'libzstd.dll',
    'libbrotlidec.dll', 'libidn2-0.dll', 'libpsl-5.dll', 'libssh2-1.dll',
    'libgif-7.dll', 'libjpeg-8.dll', 'libopenjp2-7.dll', 'libpng16-16.dll',
    'libwebp-7.dll', 'libwebpmux-3.dll', 'libdeflate.dll', 'libjbig-0.dll',
    'libLerc.dll', 'libbrotlicommon.dll', 'libintl-8.dll', 'libunistring-5.dll',
    'libsharpyuv-0.dll'
)
foreach ($name in $ocrFiles) {
    Copy-Item -LiteralPath (Join-Path $extractRoot $name) -Destination $runtimeRoot -Force
}
Copy-Item -LiteralPath $englishModel, $chineseModel -Destination $modelRoot -Force

$ocrNotices = Join-Path $runtimeRoot 'licenses'
New-Item -ItemType Directory -Path $ocrNotices -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $extractRoot 'doc/LICENSE') -Destination (Join-Path $ocrNotices 'Tesseract-LICENSE.txt') -Force
Copy-Item -LiteralPath (Join-Path $extractRoot 'doc/AUTHORS') -Destination (Join-Path $ocrNotices 'Tesseract-AUTHORS.txt') -Force
Copy-Item -LiteralPath $modelLicense -Destination (Join-Path $ocrNotices 'tessdata_fast-LICENSE.txt') -Force

$engine = Join-Path $runtimeRoot 'tesseract.exe'
$languages = & $engine --list-langs --tessdata-dir $modelRoot
if ($LASTEXITCODE -ne 0 -or 'chi_sim' -notin $languages -or 'eng' -notin $languages) {
    throw 'Tesseract could not load the packaged language files.'
}
Write-Host "Offline OCR ready at $runtimeRoot"
