# FILE: build.ps1 (Final, Most Compatible Version)

# Stop the script if any command fails
$ErrorActionPreference = "Stop"

# Define the Python version and architecture for AWS Lambda
$PYTHON_VERSION = "3.9"
$ARCHITECTURE = "x86_64"

# Get the directory where this script is located
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# Define source and distribution directories
$sourceDir = Join-Path $scriptDir "src"
$distDir = Join-Path $scriptDir "dist"

# Clean up the distribution directory from previous builds
if (Test-Path $distDir) {
    Write-Host "Cleaning up old distribution directory..."
    # THIS IS THE FIX: Using the full command path and piping to be more compatible
    Get-ChildItem -Path $distDir | Microsoft.PowerShell.Management\Remove-Item -Recurse -Force
}
New-Item -ItemType Directory -Path $distDir | Out-Null

# Find all subdirectories in 'src' that represent a Lambda function
$lambdaFunctions = Get-ChildItem -Path $sourceDir -Directory

Write-Host "Found $($lambdaFunctions.Count) Lambda functions to build."

# Loop through each function directory and build its package
foreach ($function in $lambdaFunctions) {
    $functionName = $function.Name
    $functionDir = $function.FullName
    $requirementsFile = Join-Path $functionDir "requirements.txt"
    $zipOutputPath = Join-Path $distDir "$($functionName).zip"
    
    Write-Host "--- Building package for: $functionName ---"
    
    $stagingDir = Join-Path $distDir "staging_$functionName"
    New-Item -ItemType Directory -Path $stagingDir | Out-Null
    
    Write-Host "  - Copying source code..."
    Copy-Item -Path "$($functionDir)\*" -Destination $stagingDir -Recurse -Exclude "requirements.txt"
    
    if (Test-Path $requirementsFile) {
        Write-Host "  - Installing dependencies using Docker for Linux compatibility..."
        
        docker run --rm `
            --entrypoint /bin/sh `
            -v "$($requirementsFile):/var/task/requirements.txt:ro" `
            -v "$($stagingDir):/var/task/" `
            "public.ecr.aws/lambda/python:${PYTHON_VERSION}-${ARCHITECTURE}" `
            -c "pip install -r requirements.txt -t . && exit"
            
        Write-Host "  - Dependencies installed successfully."
    } else {
        Write-Host "  - No requirements.txt found for this function."
    }
    
    Write-Host "  - Creating deployment package at $($zipOutputPath)..."
    Compress-Archive -Path "$($stagingDir)\*" -DestinationPath $zipOutputPath -Force
    
    # THIS IS THE SECOND FIX: Using the more compatible command again
    Microsoft.PowerShell.Management\Remove-Item -Path $stagingDir -Recurse -Force
    
    Write-Host "--- Successfully built package for: $functionName ---"
}

Write-Host "`nBuild process complete. All packages are in the '$distDir' directory."