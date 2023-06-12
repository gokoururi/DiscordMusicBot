# Discord Music Bot (Botasa-chan)

## Setup
Create python virtual environment
```
cd DiscordMusicBot
python3 -m venv venv
```

Activate virtual environment
```
source venv/bin/activate
```

```
pip3 install -r requirements.txt
```

## Run
```
nohup python3 main.py 2>&1 > output.log &
```

## Update pip package youtube-dl
pip install --upgrade --force-reinstall "git+https://github.com/ytdl-org/youtube-dl.git"