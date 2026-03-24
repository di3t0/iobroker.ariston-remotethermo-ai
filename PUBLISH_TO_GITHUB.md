# Publish to GitHub

## Windows CMD

```cmd
cd C:\path\to\repo
 git init
 git add .
 git commit -m "v1.2.0 stable release"
 git branch -M main
 git remote add origin https://github.com/YOURNAME/iobroker.ariston-remotethermo-ai.git
 git push -u origin main
```

## Existing repository

```cmd
git clone https://github.com/YOURNAME/iobroker.ariston-remotethermo-ai.git
xcopy /E /I /Y C:\source\folder\* C:\path\to\cloned\repo\
cd C:\path\to\cloned\repo
 git add .
 git commit -m "v1.2.0 stable release"
 git push
```
