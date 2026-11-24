$ErrorActionPreference = "Stop"
$repo = "C:\Users\yinru\WorkBuddy\2026-11-23-22-23-08\firmware\repos\Atlan4\Scope v0.28a"
Set-Location -LiteralPath $repo
# WorkBuddy 原方工具链：GCC 14.2 rel1
$bin  = "C:\Program Files (x86)\Arm GNU Toolchain arm-none-eabi\14.2 rel1\bin"
$gcc  = "$bin\arm-none-eabi-gcc.exe"
$objc = "$bin\arm-none-eabi-objcopy.exe"
if(-not (Test-Path $gcc)){ throw "GCC14 not found at $gcc" }
$OBJ  = "$repo\build\Debug\GNU_ARM-Linux"
New-Item -ItemType Directory -Force -Path $OBJ | Out-Null

# 基础 CFLAGS（与记录一致）
$cflags = @("-Wall","-Wno-write-strings","-Wno-char-subscripts","-fno-stack-protector","-DNO_STDLIB=1","-mcpu=arm926ej-s","-O3","-mfloat-abi=soft","-fno-tree-loop-distribute-patterns")
# PC_interface.c 额外规避 GCC14 把 my_strlen/strcmp 改写成 libc 调用
$extra = @{ "PC_interface" = @("-fno-tree-loop-distribute-patterns") }

$csrc = @("DS3231","PC_interface","ccu_control","cdc_class","diskio","display_control","display_lib","ff","ffunicode","fnirsi_1013d_scope","font_0","font_2","font_3","font_4","font_5","fpga_control","icons","interrupt","mass_storage_class","menu","power_and_battery","ref_and_math","scope_functions","scope_control","chinese_font","sd_card_interface","sin_cos_math","spi_control","statemachine","test","timer","touchpanel","usb_interface","variables")
$ssrc = @("memcmp","memcpy","memmove","memset","start")

$now = Get-Date
@"
#ifndef BUILD_TIME_H
#define BUILD_TIME_H
#define COMPILE_YEAR $($now.ToString("yy"))
#define COMPILE_MONTH $([int]$now.Month)
#define COMPILE_DAY $([int]$now.Day)
#define COMPILE_HOUR $([int]$now.Hour)
#define COMPILE_MIN $([int]$now.Minute)
#define COMPILE_SEC $([int]$now.Second)
#endif
"@ | Set-Content -LiteralPath "$repo\build_time.h" -Encoding ascii

$objs = @(); $fail = 0
foreach($s in $csrc){
  $o = "$OBJ\$s.o"
  $xf = if($extra.ContainsKey($s)){ $extra[$s] } else { @() }
  $args1 = @($cflags) + @($xf) + @("-g","-c","-o",$o,"$s.c")
  & $gcc @args1 2>&1 | Where-Object { "$_" -match "error|undefined" }
  if($LASTEXITCODE -ne 0 -or -not (Test-Path $o)){ Write-Host "[FAIL C] $s"; $fail++; continue }
  $objs += $o
}
foreach($s in $ssrc){
  $o = "$OBJ\$s.o"
  & $gcc -x assembler-with-cpp -c -O0 -mcpu=arm926ej-s -mthumb -Wall -fmessage-length=0 -g -o $o "$s.s" 2>&1 | Where-Object { "$_" -match "error|undefined" }
  if($LASTEXITCODE -ne 0 -or -not (Test-Path $o)){ Write-Host "[FAIL S] $s"; $fail++; continue }
  $objs += $o
}
Write-Host "=== objects ok: $($objs.Count) failed: $fail ==="
if($objs.Count -lt 39){ exit 1 }

# start.o 由链接脚本按相对路径引用，命令行不重复传
$linkobjs = @($objs | Where-Object { (Split-Path $_ -Leaf) -ne "start.o" })
$dist = "$repo\dist\Debug\GNU_ARM-Linux"
New-Item -ItemType Directory -Force -Path $dist | Out-Null
$elf = "$dist\fnirsi_1013d_scope.elf"
& $gcc -o $elf @linkobjs "-T./fnirsi_1013d.ld" -nostdlib -lgcc 2>&1 | Where-Object { "$_" -notmatch "section.*\.text|RWX|warning: " }
Write-Host "=== link exit: $LASTEXITCODE ==="
if(Test-Path $elf){
  $scopebin = "$dist\fnirsi_1013d_scope.bin"
  & $objc -O binary $elf $scopebin
  Write-Host ("=== objcopy exit: $LASTEXITCODE  scope.bin bytes: " + (Get-Item $scopebin).Length)
}
