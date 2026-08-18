#!/bin/bash

# 1. 작업 디렉토리로 이동 (Git 프로젝트 폴더 위치)
cd D:/python

# 2. 바뀐 파일을 Git에 추가
git add .

# 3. 커밋(변경사항 저장) 메시지를 자동으로 작성
commit_message="Auto commit on $(date '+%Y-%m-%d %H:%M:%S')"
git commit -m "$commit_message"

# 4. 인터넷(Git 서버)으로 푸시
git push origin main
