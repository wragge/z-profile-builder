#!/usr/bin/env python
# coding: utf-8

# In[25]:


from pyzotero import zotero
from dotenv import load_dotenv
import os
from pathlib import Path
import nh3
from slugify import slugify
import json
import re
import tomli_w

load_dotenv()

ZOTERO_KEY = os.getenv("ZOTERO_KEY", "")
ZOTERO_ID = os.getenv("ZOTERO_ID", "")
BASE_URL = os.getenv("BASE_URL", "/")


# In[37]:


OA_VERSIONS = {
    "vor": {"name": "version of record", "position": 1},
    "aam": {"name": "author accepted manuscript", "position": 2},
    "preprint": {"name": "preprint", "position": 3}
}

TESTING = False

class ProcessingError(Exception):
    """Exception raised for custom error scenarios."""

    def __init__(self, message):
        self.message = message
        super().__init__(self.message)

class ZProfileBuilder:
    def __init__(self, zotero_id, zotero_key, output_path="."):
        self.zot = zotero.Zotero(zotero_id, "user", zotero_key)
        self.output_path = output_path

    def get_zotero_data(self, collection_name="z-profile"):
        colls = self.zot.everything(self.zot.collections_top())
        try:
            collection_id = [c for c in colls if c["data"]["name"] == collection_name][0]["key"]
        except (IndexError, KeyError):
            raise ProcessingError(f"A Zotero collection named '{collection_name}' was not found.")
        else:
            items = self.zot.everything(self.zot.collection_items_top(collection_id, include="data,bib", linkwrap=1))
            return items


    def check_label(self, item, label):
        if item["data"]["title"].lower() == label or label in [t["tag"] for t in item["data"]["tags"]]:
            return True
        return False

    def dump_attachment(self, attachment):
        parts = attachment["data"]["filename"].split(".")
        filename = f"{slugify(parts[0])}.{parts[1]}"
        if not TESTING:
            self.zot.dump(attachment["key"], filename, Path(self.output_path, "static"))
        return filename

    def get_version(self, attachments, version):
        versions = []
        version_label = OA_VERSIONS[version]["name"]
        version_position = OA_VERSIONS[version]["position"]
        for attachment in attachments:
            att_tags = [t["tag"].lower() for t in attachment["data"]["tags"]]
            if version in att_tags:
                if attachment["data"].get("contentType") ==  "application/pdf":
                    att_filename = self.dump_attachment(attachment)
                    versions.append({"title": attachment["data"].get("title", ""), "version": version_label, "type": "pdf", "url": att_filename, "version_position": version_position, "type_position": 1})
                elif attachment["data"].get("linkMode") == "linked_url":
                    versions.append({"title": attachment["data"].get("title", ""), "version": version_label, "type": "link", "url": attachment["data"]["url"], "version_position": version_position, "type_position": 2})
        return versions

    def get_extra(self, item):
        extra_data = {}
        fields = item["data"].get("extra", "").split("\n")
        for field in [f for f in fields if f]:
            try:
                key, value = field.split(":")
            except ValueError:
                pass
            else:
                extra_data[key.strip()] = value.strip()
        return extra_data

    def check_for_bio(self, items):
        # Check that the Zotero collection has an item tagged "bio"
        has_bio = False
        for item in items:
            if self.check_label(item, "bio"):
                has_bio = True
                break
        return has_bio

    def process_bio(self, item):
        # Container for front matter for index page
        bio_fm = {"extra":{}}
        # Zola config defaults
        zola_config = {
            "base_url": BASE_URL,
            # Need a proper base url for this to work
            # TO DO: option to add domain to Zotero bio
            "generate_feeds": False
        }
        accounts = []
        # Get name from creators
        author = item["data"]["title"]
        bio_fm["title"] = author
        zola_config["author"] = author
        zola_config["title"] = f"{author}: research profile"
        # Get tagline from shortTitle
        if tagline := item["data"]["shortTitle"]:
            bio_fm["extra"]["tagline"] = tagline
        # Looping through all attachments to the bio
        for attachment in self.zot.children(item["key"], itemType="attachment"):
            # If it's a PDF use as CV
            if attachment["data"].get("contentType") ==  "application/pdf":
                filename = self.dump_attachment(attachment)
                bio_fm["extra"]["cv"] = filename
            # If it's an image use as avatar
            elif attachment["data"].get("contentType") ==  "image/jpeg":
                filename = self.dump_attachment(attachment)
                bio_fm["extra"]["portrait"] = filename
            # Handle accounts
            elif attachment["data"].get("linkMode") == "linked_url":
                accounts.append({"account": attachment["data"]["title"], "url": attachment["data"]["url"]})
        bio_fm["extra"]["links"] = accounts
        # Add fields from Zotero's extra field
        extra_data = self.get_extra(item)
        bio_fm["extra"].update(extra_data)
        # Add the first attached note as the text of the home page
        try:
            bio_note = self.zot.children(item["key"], itemType="note")[0]
            cleaned_note = nh3.clean(bio_note["data"]["note"], tags={"b", "i", "p", "a", "ul", "li", "quote"})
        except IndexError:
            cleaned_note = ""
        return zola_config, bio_fm, cleaned_note

    def process_publication(self, item):
        # Default page front matter
        pub_fm = {"extra":{}, "template": "article.html"}
        # Create slugified version of title for page name
        fileslug = f"{slugify(item["data"]["title"])}-{item["key"]}"
        filename = f"{fileslug}.md"
        # Clean unwanted tags from citation
        pub_fm["extra"]["citation"] = nh3.clean(item["bib"], tags={"i", "a"}).strip()
        # Remove urls from citation
        citation_no_link = nh3.clean(item["bib"], tags={"i"})
        pub_fm["extra"]["citation_no_link"] = re.sub(r"http[^\s]+", "", citation_no_link).strip()
        # List all tags
        tags = [t["tag"].lower() for t in item["data"]["tags"]]
        # Set page title
        pub_fm["title"] = item["data"]["title"]
        # This date will be used in the RSS feed
        pub_fm["date"] = item["data"]["dateModified"]
        # Publication date
        pub_fm["extra"]["date"] = item["meta"].get("parsedDate", "")
         # Publication year for grouping
        pub_fm["extra"]["year"] = item["meta"].get("parsedDate", "")[:4]
        # Add other values to front matter
        pub_fm["extra"]["metadata"] = item["data"]
        # Set default OA status
        oa_status = ""
        if "open access" in tags:
            oa_status = "open access"
        # Get attachments to look for OA versions
        versions = []
        attachments = self.zot.children(item["key"], itemType="attachment")
        for version_type in ["vor", "aam", "preprint"]:
            version = self.get_version(attachments, version_type)
            if version:
                versions.extend(version)
                if not oa_status:
                    oa_status = f"open access: {OA_VERSIONS[version_type]["name"]}"
        pub_fm["extra"]["oa_status"] = oa_status
        pub_fm["extra"]["versions"] = versions
        selected = True if "selected" in tags else False
        article = {"filename": filename, "citation": pub_fm["extra"]["citation_no_link"], "oa_status": oa_status,"selected": selected, "year": pub_fm["extra"]["year"]}
        return pub_fm, article, filename

    def process_items(self, items):
        articles = []
        Path(self.output_path, "content", "publications").mkdir(exist_ok=True, parents=True)
        for item in items:
            # Get the bio item
            if self.check_label(item, "bio"):
                zola_config, bio_fm, cleaned_note = self.process_bio(item)
                # Write the content of the home page
                Path(self.output_path, "content", "_index.md").write_text("+++\n" + tomli_w.dumps(bio_fm) + "+++\n\n" + cleaned_note)
                # Write the config file
                Path(self.output_path, "zola.toml").write_text(tomli_w.dumps(zola_config))
            else:
                pub_fm, article, filename = self.process_publication(item)
                articles.append(article)
                Path(self.output_path, "content", "publications", filename).write_text("+++\n" + tomli_w.dumps(pub_fm) + "+++\n")
        Path(self.output_path, "content", "articles.json").write_text(json.dumps(articles))

    def build_site(self, collection_name="z-profile"):
        items = self.get_zotero_data(collection_name)
        if not items:
            raise ProcessingError(f"The Zotero collection named '{collection_name}' is empty.")
        has_bio = self.check_for_bio(items)
        if not has_bio:
            raise ProcessingError(f"The Zotero collection named '{collection_name}' doesn't include an item tagged 'bio'.")
        self.process_items(items)


# In[38]:


if __name__ == "__main__":
    builder = ZProfileBuilder(ZOTERO_ID, ZOTERO_KEY)
    builder.build_site()


# In[ ]:




