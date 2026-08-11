param(
    [Parameter(Mandatory = $true)]
    [string]$TypeName,

    [Parameter(Mandatory = $true)]
    [string]$MethodName,

    [string]$AssemblyPath = (
        Join-Path $PSScriptRoot "..\camera_tools\IGCSClient.exe"
    )
)

$ErrorActionPreference = "Stop"

$assemblyPathResolved = (Resolve-Path -LiteralPath $AssemblyPath).Path
$assemblyDirectory = Split-Path -Parent $assemblyPathResolved

Get-ChildItem -LiteralPath $assemblyDirectory -Filter "*.dll" |
    ForEach-Object {
        try {
            [void][Reflection.Assembly]::LoadFrom($_.FullName)
        }
        catch {
            # Some optional assemblies are loaded only by specific UI pages.
        }
    }

$assembly = [Reflection.Assembly]::LoadFrom($assemblyPathResolved)
$type = $assembly.GetType($TypeName, $true)

$singleByte = @{}
$doubleByte = @{}
[Reflection.Emit.OpCodes].GetFields(
    [Reflection.BindingFlags]"Public,Static"
) | ForEach-Object {
    $opcode = $_.GetValue($null)
    $value = ([int]$opcode.Value) -band 0xFFFF
    if ($value -lt 0x100) {
        $singleByte[[byte]$value] = $opcode
    }
    elseif (($value -band 0xFF00) -eq 0xFE00) {
        $doubleByte[[byte]($value -band 0xFF)] = $opcode
    }
}

function Resolve-MetadataToken {
    param(
        [Reflection.Module]$Module,
        [int]$Token,
        [Reflection.Emit.OperandType]$OperandType
    )

    try {
        switch ($OperandType) {
            "InlineString" {
                return '"' + $Module.ResolveString($Token) + '"'
            }
            "InlineSig" {
                return [BitConverter]::ToString($Module.ResolveSignature($Token))
            }
            default {
                return $Module.ResolveMember($Token).ToString()
            }
        }
    }
    catch {
        return ("token 0x{0:X8}" -f $Token)
    }
}

function Get-MethodIL {
    param([Reflection.MethodBase]$Method)

    $body = $Method.GetMethodBody()
    if ($null -eq $body) {
        Write-Output "$Method has no managed IL body."
        return
    }

    Write-Output ""
    Write-Output ("METHOD {0}" -f $Method)
    $bytes = $body.GetILAsByteArray()
    $position = 0

    while ($position -lt $bytes.Length) {
        $offset = $position
        $first = $bytes[$position]
        $position += 1
        if ($first -eq 0xFE) {
            $opcode = $doubleByte[$bytes[$position]]
            $position += 1
        }
        else {
            $opcode = $singleByte[$first]
        }

        if ($null -eq $opcode) {
            throw "Unknown IL opcode at offset $offset"
        }

        $operand = ""
        switch ($opcode.OperandType) {
            "InlineNone" {}
            "ShortInlineBrTarget" {
                $delta = [int]$bytes[$position]
                if ($delta -gt 127) {
                    $delta -= 256
                }
                $position += 1
                $operand = "IL_{0:X4}" -f ($position + $delta)
            }
            "InlineBrTarget" {
                $delta = [BitConverter]::ToInt32($bytes, $position)
                $position += 4
                $operand = "IL_{0:X4}" -f ($position + $delta)
            }
            "ShortInlineI" {
                $operand = [int]$bytes[$position]
                if ($operand -gt 127) {
                    $operand -= 256
                }
                $position += 1
            }
            "InlineI" {
                $operand = [BitConverter]::ToInt32($bytes, $position)
                $position += 4
            }
            "InlineI8" {
                $operand = [BitConverter]::ToInt64($bytes, $position)
                $position += 8
            }
            "ShortInlineR" {
                $operand = [BitConverter]::ToSingle($bytes, $position)
                $position += 4
            }
            "InlineR" {
                $operand = [BitConverter]::ToDouble($bytes, $position)
                $position += 8
            }
            "ShortInlineVar" {
                $operand = $bytes[$position]
                $position += 1
            }
            "InlineVar" {
                $operand = [BitConverter]::ToUInt16($bytes, $position)
                $position += 2
            }
            "InlineSwitch" {
                $count = [BitConverter]::ToInt32($bytes, $position)
                $position += 4
                $base = $position + (4 * $count)
                $targets = for ($index = 0; $index -lt $count; $index++) {
                    $delta = [BitConverter]::ToInt32(
                        $bytes,
                        $position + (4 * $index)
                    )
                    "IL_{0:X4}" -f ($base + $delta)
                }
                $position = $base
                $operand = $targets -join ", "
            }
            default {
                $token = [BitConverter]::ToInt32($bytes, $position)
                $position += 4
                $operand = Resolve-MetadataToken `
                    -Module $Method.Module `
                    -Token $token `
                    -OperandType $opcode.OperandType
            }
        }

        Write-Output (
            "IL_{0:X4}: {1,-12} {2}" -f $offset, $opcode.Name, $operand
        )
    }
}

$flags = [Reflection.BindingFlags]"Public,NonPublic,Static,Instance,DeclaredOnly"
$methods = @(
    $type.GetMethods($flags) | Where-Object Name -eq $MethodName
)
if ($MethodName -eq ".ctor") {
    $methods = @($type.GetConstructors($flags))
}

if ($methods.Count -eq 0) {
    throw "Method '$MethodName' was not found on '$TypeName'."
}

$methods | ForEach-Object { Get-MethodIL $_ }
