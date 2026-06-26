@echo off
echo ===================================
echo  AI 자동매매 시스템 업데이트 중...
echo ===================================

powershell -Command "Invoke-WebRequest -Uri 'https://github.com/hiyum/aaaa/archive/refs/heads/claude/prompt-explanation-hjr0ls.zip' -OutFile 'update_temp.zip'"

if not exist update_temp.zip (
    echo 다운로드 실패. 인터넷 연결을 확인하세요.
    pause
    exit
)

powershell -Command "Expand-Archive -Path 'update_temp.zip' -DestinationPath 'update_temp_folder' -Force"

xcopy /E /Y /I "update_temp_folder\aaaa-claude-prompt-explanation-hjr0ls\*" "."

rmdir /S /Q update_temp_folder
del update_temp.zip

echo ===================================
echo  업데이트 완료!
echo  python app.py 를 실행하세요.
echo ===================================
pause
