# Scan satu halaman dari Epson L3210 lewat WIA, simpan sebagai JPG dan PNG.
# Dipanggil scan_server.py; mencetak satu baris JSON (ok / error).
#   powershell -NoProfile -ExecutionPolicy Bypass -File scan_page.ps1 -OutBase C:\...\page-001 -Dpi 200 -Mode warna

param(
    [Parameter(Mandatory = $true)] [string]$OutBase,
    [int]$Dpi = 200,
    [ValidateSet("warna", "abu", "hitamputih")] [string]$Mode = "warna",
    [string]$DeviceLike = "*L3210*"
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Drawing

# Hasil mentah (BMP dari driver) -> JPG + PNG lewat .NET. Konversi WIA.ImageProcess
# gagal saat dijalankan dari service ("Unable to cast ... Int32 to String").
function Save-JpgPng([string]$src, [string]$base) {
    $img = [System.Drawing.Image]::FromFile($src)
    try {
        $codec = [System.Drawing.Imaging.ImageCodecInfo]::GetImageEncoders() | Where-Object { $_.MimeType -eq "image/jpeg" }
        $params = New-Object System.Drawing.Imaging.EncoderParameters 1
        $params.Param[0] = New-Object System.Drawing.Imaging.EncoderParameter ([System.Drawing.Imaging.Encoder]::Quality), ([long]88)
        $img.Save("$base.jpg", $codec, $params)
        $img.Save("$base.png", [System.Drawing.Imaging.ImageFormat]::Png)
    } finally { $img.Dispose() }
}

function Set-WiaProperty($item, [string]$id, [int]$value) {
    try { $item.Properties.Item($id).Value = $value } catch { }   # ditolak driver -> bawaan
}

$step = "mulai"
try {
    $step = "cari scanner"
    $dm = New-Object -ComObject WIA.DeviceManager
    $info = $null
    foreach ($d in $dm.DeviceInfos) {
        if ($d.Properties.Item("Name").Value -like $DeviceLike) { $info = $d; break }
    }
    if (-not $info) { throw "Scanner tidak ditemukan. Pastikan printer EPSON L3210 menyala dan terhubung ke server." }

    $step = "sambung ke scanner"
    $item = $info.Connect().Items.Item(1)
    # 6146 = jenis gambar (1 warna, 2 abu-abu, 4 hitam-putih), 6147/6148 = DPI.
    Set-WiaProperty $item "6146" (@{ warna = 1; abu = 2; hitamputih = 4 }[$Mode])
    Set-WiaProperty $item "6147" $Dpi
    Set-WiaProperty $item "6148" $Dpi

    $step = "scan"
    $sw = [Diagnostics.Stopwatch]::StartNew()
    $raw = $item.Transfer()
    $width = $raw.Width; $height = $raw.Height

    $step = "simpan hasil"
    $tmp = "$OutBase.raw"
    foreach ($f in $tmp, "$OutBase.jpg", "$OutBase.png") { if (Test-Path $f) { Remove-Item $f -Force } }
    $raw.SaveFile($tmp)
    $step = "ubah ke JPG/PNG"
    try { Save-JpgPng $tmp $OutBase } finally { Remove-Item $tmp -Force -ErrorAction SilentlyContinue }

    @{ ok = $true; width = $width; height = $height; seconds = [math]::Round($sw.Elapsed.TotalSeconds, 1) } |
        ConvertTo-Json -Compress
} catch {
    @{ ok = $false; error = "Gagal saat ${step}: $($_.Exception.Message)" } | ConvertTo-Json -Compress
}
