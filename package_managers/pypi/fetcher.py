import json
import os
import time
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from typing import List, Optional

import requests

from core.config import Config
from core.fetcher import Data, Fetcher
from core.logger import Logger


class SimpleIndexParser(HTMLParser):
    """Parser for PyPI's simple index page to extract package names."""
    def __init__(self):
        super().__init__()
        self.packages = []

    def handle_starttag(self, tag: str, attrs: List[tuple]):
        if tag == "a":
            for attr, value in attrs:
                if attr == "href":
                    # Package names are the href values
                    self.packages.append(value)


class PyPIFetcher(Fetcher):
    """Custom fetcher for PyPI that handles both simple index and JSON API."""
    
    def __init__(self, name: str, config: Config):
        super().__init__(name, config)
        self.session = requests.Session()
        self.base_url = "https://pypi.org"
        self.rate_limit_delay = 1  # seconds between requests
        # Override output path to use absolute path
        self.output = "/data/pypi"
        
    def _get_package_list(self) -> List[str]:
        """Fetch list of all packages from PyPI simple index."""
        url = f"{self.base_url}/simple/"
        response = self.session.get(url)
        response.raise_for_status()
        
        parser = SimpleIndexParser()
        parser.feed(response.text)
        return parser.packages

    def _get_package_data(self, package_name: str) -> Optional[dict]:
        """Fetch JSON data for a specific package."""
        # Remove any /simple/ prefix if present
        package_name = package_name.replace('/simple/', '')
        # Remove any trailing slash
        package_name = package_name.rstrip('/')
        url = f"{self.base_url}/pypi/{package_name}/json"
        
        try:
            response = self.session.get(url)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            self.logger.error(f"Error fetching {package_name}: {e}")
            return None
        except json.JSONDecodeError as e:
            self.logger.error(f"Error parsing JSON for {package_name}: {e}")
            return None

    def fetch(self) -> List[Data]:
        """
        Fetch package data from PyPI:
        1. Get list of all packages from simple index
        2. Fetch JSON data for each package
        3. Save data in batches
        """
        packages = self._get_package_list()
        self.logger.log(f"Found {len(packages)} packages")
        
        # For testing, limit to a small number of packages
        if self.test:
            packages = packages[:100]
            self.logger.log("Test mode: limiting to 100 packages")

        # Prepare the output directories
        now = datetime.now().strftime("%Y-%m-%d")
        dated_dir = os.path.join(self.output, now)
        
        # Create dated directory
        os.makedirs(dated_dir, exist_ok=True)

        # Remove latest symlink if it exists
        latest_symlink = os.path.join(self.output, "latest")
        if os.path.exists(latest_symlink):
            if os.path.islink(latest_symlink):
                os.remove(latest_symlink)
            else:
                # If it's a directory, remove it recursively
                import shutil
                shutil.rmtree(latest_symlink)

        batch_size = 20
        current_batch = []
        batch_num = 1
        files = []

        for i, package_name in enumerate(packages, 1):
            # Fetch package data
            package_data = self._get_package_data(package_name)
            if package_data:
                current_batch.append(package_data)

            # Save batch if it reaches batch_size or is the last package
            if len(current_batch) >= batch_size or i == len(packages):
                if current_batch:  # Only save if we have data
                    batch_content = json.dumps(current_batch).encode('utf-8')
                    
                    # Save to dated directory
                    batch_name = f"packages_batch_{batch_num}.json"
                    dated_file = os.path.join(dated_dir, batch_name)
                    with open(dated_file, 'wb') as f:
                        f.write(batch_content)
                    
                    # Create Data object for transformer
                    data = Data(
                        file_path="",  # Root directory
                        file_name=batch_name,
                        content=batch_content
                    )
                    files.append(data)
                    
                    self.logger.log(f"Saved batch {batch_num} ({len(current_batch)} packages)")
                    
                    # Reset for next batch
                    current_batch = []
                    batch_num += 1

            # Rate limiting
            time.sleep(self.rate_limit_delay)

            if i % 10 == 0 or i == len(packages):
                self.logger.log(f"Processed {i}/{len(packages)} packages")

        return files
