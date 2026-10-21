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
$JPEG = "{B96B3CAE-0728-11D3-9D7B-0000F81EF32E}"
$PNG = "{B96B3CAF-0728-11D3-9D7B-0000F81EF32E}"

function Convert-Image($image, $format, $quality) {
    $proc = New-Object -ComObject WIA.ImageProcess
    $proc.Filters.Add($proc.FilterInfos.Item("Convert").FilterID)
    $proc.Filters.Item(1).Properties.Item("FormatID").Value = $format
    if ($quality) { $proc.Filters.Item(1).Properties.Item("Quality").Value = $quality }
    return $proc.Apply($image)
}

function Set-WiaProperty($item, [string]$id, [int]$value) {
    try { $item.Properties.Item($id).Value = $value } catch { }   # ditolak driver -> bawaan
}

try {
    $dm = New-Object -ComObject WIA.DeviceManager
    $info = $null
    foreach ($d in $dm.DeviceInfos) {
        if ($d.Properties.Item("Name").Value -like $DeviceLike) { $info = $d; break }
    }
    if (-not $info) { throw "Scanner tidak ditemukan. Pastikan printer EPSON L3210 menyala dan terhubung ke server." }

    $item = $info.Connect().Items.Item(1)
    # 6146 = jenis gambar (1 warna, 2 abu-abu, 4 hitam-putih), 6147/6148 = DPI.
    Set-WiaProperty $item "6146" (@{ warna = 1; abu = 2; hitamputih = 4 }[$Mode])
    Set-WiaProperty $item "6147" $Dpi
    Set-WiaProperty $item "6148" $Dpi

    $sw = [Diagnostics.Stopwatch]::StartNew()
    $raw = $item.Transfer()
    $jpg = if ($raw.FormatID -eq $JPEG) { $raw } else { Convert-Image $raw $JPEG 88 }
    $png = if ($raw.FormatID -eq $PNG) { $raw } else { Convert-Image $raw $PNG $null }
    foreach ($ext in "jpg", "png") { if (Test-Path "$OutBase.$ext") { Remove-Item "$OutBase.$ext" -Force } }
    $jpg.SaveFile("$OutBase.jpg")
    $png.SaveFile("$OutBase.png")

    @{ ok = $true; width = $raw.Width; height = $raw.Height; seconds = [math]::Round($sw.Elapsed.TotalSeconds, 1) } |
        ConvertTo-Json -Compress
} catch {
    @{ ok = $false; error = $_.Exception.Message } | ConvertTo-Json -Compress
}
