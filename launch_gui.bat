@echo off
rem Launch the audio-score-follower launcher GUI (double-click to run)
cd /d "%~dp0"
python -m audio_score_follower.main
if errorlevel 1 pause
