@echo off
REM Generate self-signed TLS certificate for development on Windows
REM Requires OpenSSL (installed via Chocolatey or from https://slproweb.com/products/Win32OpenSSL.html)

echo Generating self-signed TLS certificate for Tailscale...

openssl req -x509 -newkey rsa:4096 -nodes ^
  -out C:\tmp\cert.pem ^
  -keyout C:\tmp\key.pem ^
  -days 365 ^
  -subj "/C=US/ST=CA/L=Local/O=RemoteGPU/CN=degitaar.local"

if exist C:\tmp\cert.pem if exist C:\tmp\key.pem (
    echo X TLS certificate generated:
    echo   - C:\tmp\cert.pem
    echo   - C:\tmp\key.pem
    echo.
    echo Server will use TLS when connecting to Tailscale
) else (
    echo X Certificate generation failed
    echo Check that OpenSSL is installed and in PATH
    pause
    exit /b 1
)

pause
