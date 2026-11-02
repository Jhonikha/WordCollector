# Smart Vocab Builder

Android app with Flask backend for vocabulary building with clipboard monitoring.

## Features
- Auto-clipboard monitoring (background service)
- Batch AI analysis (Gemini API)
- Bengali-English vocabulary sheet
- One-tap clipboard add

## Build Instructions

### Option 1: GitHub Actions (Recommended - No computer needed)
1. Fork this repository
2. Go to Actions tab
3. Click "Build Android APK"
4. Wait 10-15 minutes
5. Download APK from Artifacts

### Option 2: Local Build
```bash
./gradlew assembleDebug
```

## Installation
1. Download `app-debug.apk`
2. Enable "Unknown Sources" in Android settings
3. Install APK

## Usage
1. Open app
2. Copy any English text
3. Words auto-added to queue (notification shows)
4. Tap "Start Batch Analysis"
5. Review and save words
