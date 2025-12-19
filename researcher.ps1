param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $ArgsRest
)

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
python (Join-Path $root "researcher\\cli.py") @ArgsRest
