#!/usr/bin/env python3

# This is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

import distutils.spawn
import dotenv
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

class ChromeDriverFetcher:
    downloadsFile = "https://googlechromelabs.github.io/chrome-for-testing/known-good-versions-with-downloads.json"

    platform = None
    chromeVersion = None

    chromedriverArgs = []
    cacheDir = None
    pathToChrome = None

    def __init__(
            self,
            downloadsFile = None,
            platform = None,
            chromeVersion = None,
    ) -> None:
        if downloadsFile is not None:
            self.downloadsFile = downloadsFile

        if platform is None:
            platform = self.getPlatform()
        self.platform = platform

        if chromeVersion is None:
            chromeVersion = self.getChromeVersion()
        self.chromeVersion = chromeVersion

        self.cacheDir = '%s/cache' % ( os.path.dirname(os.path.dirname(__file__)) )
        self.getOptions()

        args = sys.argv
        args.pop(0)
        self.chromedriverArgs += args

        if (not os.path.isdir(self.cacheDir)):
            os.mkdir(self.cacheDir)

    def getAppDir(self):
        if os.name == 'posix':  # Linux/macOS
            dir = Path.home()
        elif os.name == 'nt':  # Windows
            dir = Path(os.getenv('LOCALAPPDATA'))
        else:
            raise OSError("Unsupported operating system")
        return dir

    def getPuppeteerCacheDir(self):
        # Check if the PUPPETEER_CACHE_DIR environment variable is set
        env_cache_dir = os.getenv('PUPPETEER_CACHE_DIR')

        if env_cache_dir:
            # If the environment variable is set, use it
            cache_dir = Path(env_cache_dir)
        else:
            # Otherwise, build the default cache directory path based on the OS
            if os.name == 'posix':  # Linux/macOS
                cache_dir = Path.home() / '.cache' / 'puppeteer'
            elif os.name == 'nt':  # Windows
                cache_dir = Path(os.getenv('LOCALAPPDATA')) / 'puppeteer' / 'Cache'
            else:
                raise OSError("Unsupported operating system")

        return cache_dir

    def getPuppeteerChromePath(self):
        # Support puppeteer installation (which is google's recommended way of installing)
        # First, determine the Puppeteer app / cache directory
        app_dir = self.getAppDir()
        cache_dir = self.getPuppeteerCacheDir()

        # Define the partial paths based on the operating system
        if os.name == 'posix':  # Linux/macOS
            if Path("/Applications").exists():  # macOS
                partial_path = "chrome/mac-*/chrome-mac-x64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"
            else:  # Linux
                partial_path = "chrome/linux-*/chrome-linux-x64/google-chrome"
        elif os.name == 'nt':  # Windows
            partial_path = "chrome/win-*/chrome-win/chrome.exe"
        else:
            raise OSError("Unsupported operating system")

        # Use glob to search for all possible Google Chrome for Testing apps
        google_for_testing_paths = list(app_dir.glob(partial_path))
        if not google_for_testing_paths:
            google_for_testing_paths = list(cache_dir.glob(partial_path))

        if not google_for_testing_paths:
            raise Exception("Could not find puppeteer path for Google Chrome for Testing")

        # Extract version numbers from the directory names
        versioned_paths = []
        for path in google_for_testing_paths:
            # Use regex to find version number in the path
            version_match = re.search(r'(\d+\.\d+\.\d+\.\d+)', str(path))
            if version_match:
                version = tuple(map(int, version_match.group(0).split('.')))
                versioned_paths.append((version, path))

        # Sort the paths by version number in descending order
        versioned_paths.sort(reverse=True, key=lambda x: x[0])

        # Return the path with the latest version
        return versioned_paths[0][1] if versioned_paths else None

    def getChromePath(self):
        if (self.pathToChrome is not None):
            return self.pathToChrome
        if (sys.platform == 'darwin'):
            darwinPath = "/Applications/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"
            if (os.path.exists(darwinPath)):
                return darwinPath
        elif (sys.platform == 'linux'):
            return distutils.spawn.find_executable("google-chrome-stable")
        # We didn't find "Google Chrome for Testing", so lets see if it was installed via
        # puppeteer
        puppeteerPath = self.getPuppeteerChromePath()
        if (puppeteerPath):
            return puppeteerPath
        raise Exception("Failed to find a valid Google Chrome for Testing app - if you have node installed, you can install it via npx @puppeteer/browsers install chrome@stable")

    def getChromeVersion(self):
        versionOutput = subprocess.check_output([self.getChromePath(), '--version'])
        versionOutput = versionOutput.decode()
        versionString = re.search('Google Chrome ?(for Testing)? ([0-9.]+)', versionOutput).group(2).strip()

        return versionString

    def getVersionData(self):
        response = urllib.request.urlopen(self.downloadsFile).read()
        return json.loads(response.decode('utf-8'))

    def getLegacyChromedriverUrl(self):
        version = self.chromeVersion.split('.')
        version.pop()
        version = '.'.join(version)
        latestVersionAvailableUrl = "https://chromedriver.storage.googleapis.com/LATEST_RELEASE_%s" % ( version )
        latestVersion = urllib.request.urlopen(latestVersionAvailableUrl).read().decode('utf-8')

        return "https://chromedriver.storage.googleapis.com/%s/chromedriver_%s.zip" % ( latestVersion, self.platform )

    def getChromedriverUrl(self):
        majorVersion = int(self.chromeVersion.split('.')[0])
        if (majorVersion <= 114):
            return self.getLegacyChromedriverUrl()

        versionData = self.getClosestVersionMatch()
        if (versionData is None):
            raise ValueError("Unable to find a chromedriver download for %s" % ( self.chromeVersion ))

        downloadData = versionData['downloads']
        chromedriverData = downloadData.get('chromedriver')
        if chromedriverData is None:
            raise ValueError("Unable to find a chromedriver download for %s" % ( self.chromeVersion ))

        for platformData in chromedriverData:
            if platformData.get('platform') == self.platform:
                return platformData.get('url')


    def getClosestVersionMatch(self):
        content = self.getVersionData()

        majorMinorVersion = self.chromeVersion.split('.')
        majorMinorVersion.pop()
        majorMinorVersion = '.'.join(majorMinorVersion)

        possibleVersions = []
        for versionData in content['versions']:
            if (versionData['version'] == self.chromeVersion):
                # Exact match. Return immediately.
                print("Exact match found for %s" % ( self.chromeVersion ))
                return versionData

            if (versionData['version'].startswith(majorMinorVersion)):
                print("Possible match found for %s at %s" % ( self.chromeVersion, versionData['version'] ) )
                possibleVersions.insert(0, versionData)

        # No exact version found. Try and find the closing older version instead.
        patchNumber = int(self.chromeVersion.split('.')[3])

        for versionData in possibleVersions:
            thisPatchNumber = int(versionData['version'].split('.')[3])
            if (thisPatchNumber > patchNumber):
                 # Newer version. Skip.
                 continue

            # This is not an exact match, and it's not a newer version. It's the best match.
            return versionData;

    def getPlatform(self):
        if (sys.platform == 'darwin'):
            return "mac-%s" % platform.machine().replace("x86_", "x")
        if (sys.platform.startswith('linux')):
            if (platform.machine() == 'x86_64'):
                return "linux64"
            raise RuntimeError("Chrome is not supported on 32-bit versions of Linux")
        if (sys.platform == 'win32'):
            if (platform.architecture()[0] == '64bit'):
                return "win64"
            return "win32"
        raise RuntimeError("Unable to find a chromedriver download for %s" % ( sys.platform ))

    def getZipPath(self, targetDirectory):
        return "%s/chromedriver.zip" % ( targetDirectory.name )

    def downloadChromeDriver(self):
        print("Downloading chromedriver for Chrome %s on %s" % ( self.chromeVersion, self.platform ))
        url = self.getChromedriverUrl()
        print("Using url %s" % (url))

        targetDirectory = tempfile.TemporaryDirectory()
        targetFile = self.getZipPath(targetDirectory)

        print("Fetching from %s to %s" % ( url, targetFile ))
        urllib.request.urlretrieve(url, targetFile)

        return targetDirectory

    def getPathInZip(self, targetDirectory):
        majorVersion = int(self.chromeVersion.split('.')[0])
        if (majorVersion <= 114):
            return "%s/chromedriver" % ( targetDirectory.name )

        return "%s/chromedriver-%s/chromedriver" % ( targetDirectory.name, self.platform )

    def getTargetPath(self):
        return "%s/chromedriver_%s_%s" % (
            self.cacheDir,
            self.platform,
            self.chromeVersion,
        )

    def downloadAndUnzipChromeDriver(self):
        targetDirectory = self.downloadChromeDriver()
        zipFile = self.getZipPath(targetDirectory)
        with zipfile.ZipFile(zipFile, 'r') as zip_ref:
            zip_ref.extractall(targetDirectory.name)

            targetFile = self.getTargetPath()
            shutil.copyfile(
                self.getPathInZip(targetDirectory),
                targetFile,
            )

            st = os.stat(targetFile)
            os.chmod(targetFile, st.st_mode | stat.S_IEXEC)

    def getOptions(self):
        if (not os.path.isfile(".env") and os.path.isfile('chromedriver.conf')):
            raise RuntimeError("Legacy chromedriver.conf file found. Please convert this to a .env file.")

        config = dotenv.dotenv_values(".env")

        if (config.get('EXTRA_OPTIONS') is not None):
            self.chromedriverArgs = config.get('EXTRA_OPTIONS').split(' ')

        if (config.get('PATH_TO_CACHEDIR') is not None):
            self.cacheDir = config.get('PATH_TO_CACHEDIR')

        if (config.get('PATH_TO_CHROME') is not None):
            self.pathToChrome = config.get('PATH_TO_CHROME')

        return config

    def executeDriver(self):
        driverPath = self.getTargetPath()

        # Ensure we have ChromeDriver downloaded and extracted
        if not os.path.isfile(driverPath) or not os.access(driverPath, os.X_OK):
            self.downloadAndUnzipChromeDriver()

        # Get the path to Google Chrome for Testing
        chromeBinaryPath = self.getChromePath()

        # Put the path into a config so that moodle can reference it.
        print(f"CDW_CHROME_BINARY_PATH={str(self.getChromePath())}")
        config_dir = self.getAppDir() / ".chromedriver_wrapper"
        config_file = config_dir / "config"
        config_dir.mkdir(parents=True, exist_ok=True)
        with open(config_file, 'w') as f:
            f.write(f"CDW_CHROME_BINARY_PATH={str(chromeBinaryPath)}\n")

        print(f"Chrome binary path written to: {config_file}")

        print(f"Executing ChromeDriver with args: {self.chromedriverArgs}")

        # Run ChromeDriver with the args
        subprocess.run(
            self.chromedriverArgs,
            executable=driverPath,
            shell=True,
        )
