# -*- coding: utf-8 -*-
# 构建便携更新包（发布流程用）
param([string]$Ver = "0.4.17")
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.IO.Compression.FileSystem
Set-Location (Split-Path $PSScriptRoot -Parent)
$zip = "ChinaSeal-$Ver-portable.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
[System.IO.Compression.ZipFile]::CreateFromDirectory("dist\ChinaSeal", $zip, [System.IO.Compression.CompressionLevel]::Optimal, $false)
Write-Host ("ZIP OK: " + [math]::Round((Get-Item $zip).Length/1MB,1) + " MB -> " + (Resolve-Path $zip))
