@echo off
echo Setting up Meme Generator Backend...

REM Create virtual environment if it doesn't exist
if not exist "env" (
    echo Creating virtual environment...
    python -m venv env
)

REM Activate virtual environment
echo Activating virtual environment...
call .\env\Scripts\activate

REM Install dependencies
echo Installing dependencies...
pip install -r requirements.txt

echo Setup complete!
echo.
echo To start the server, run:
echo uvicorn main:app --reload
echo.
echo To test the Gemini API, run:
echo python test_gemini.py 