# -*- coding: utf-8 -*-

# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation.

"""Extractors for https://www.facebook.com/"""

from .common import Extractor, Message, Dispatch
from .. import text, exception
from ..cache import memcache

BASE_PATTERN = r"(?:https?://)?(?:[\w-]+\.)?facebook\.com"
USER_PATTERN = (BASE_PATTERN +
                r"/(?!media/|photo/|photo.php|watch/)"
                r"(?:profile\.php\?id=|people/[^/?#]+/)?([^/?&#]+)")


class FacebookExtractor(Extractor):
    """Base class for Facebook extractors"""
    category = "facebook"
    root = "https://www.facebook.com"
    directory_fmt = ("{category}", "{username}", "{title} ({set_id})")
    filename_fmt = "{id}.{extension}"
    archive_fmt = "{id}.{extension}"

    def _init(self):
        headers = self.session.headers
        headers["Accept"] = (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,image/png,image/svg+xml,*/*;q=0.8"
        )
        headers["Sec-Fetch-Dest"] = "empty"
        headers["Sec-Fetch-Mode"] = "navigate"
        headers["Sec-Fetch-Site"] = "same-origin"

        self.fallback_retries = self.config("fallback-retries", 2)
        self.videos = self.config("videos", True)
        self.author_followups = self.config("author-followups", False)

    def decode_all(self, txt):
        return text.unescape(
            txt.encode().decode("unicode_escape")
            .encode("utf_16", "surrogatepass").decode("utf_16")
        ).replace("\\/", "/")

    def parse_set_page(self, set_page):
        directory = {
            "set_id": text.extr(
                set_page, '"mediaSetToken":"', '"'
            ) or text.extr(
                set_page, '"mediasetToken":"', '"'
            ),
            "username": self.decode_all(
                text.extr(
                    set_page, '"user":{"__isProfile":"User","name":"', '","'
                ) or text.extr(
                    set_page, '"actors":[{"__typename":"User","name":"', '","'
                )
            ),
            "user_id": text.extr(
                set_page, '"owner":{"__typename":"User","id":"', '"'
            ),
            "user_pfbid": "",
            "title": self.decode_all(text.extr(
                set_page, '"title":{"text":"', '"'
            )),
            "first_photo_id": text.extr(
                set_page,
                '{"__typename":"Photo","__isMedia":"Photo","',
                '","creation_story"'
            ).rsplit('"id":"', 1)[-1] or
            text.extr(
                set_page, '{"__typename":"Photo","id":"', '"'
            )
        }

        if directory["user_id"].startswith("pfbid"):
            directory["user_pfbid"] = directory["user_id"]
            directory["user_id"] = (
                text.extr(
                    set_page, '"actors":[{"__typename":"User","id":"', '"') or
                directory["set_id"].split(".")[1])

        return directory

    def parse_photo_page(self, photo_page):
        photo = {
            "id": text.extr(
                photo_page, '"__isNode":"Photo","id":"', '"'
            ),
            "set_id": text.extr(
                photo_page,
                '"url":"https:\\/\\/www.facebook.com\\/photo\\/?fbid=',
                '"'
            ).rsplit("&set=", 1)[-1],
            "username": self.decode_all(text.extr(
                photo_page, '"owner":{"__typename":"User","name":"', '"'
            )),
            "user_id": text.extr(
                photo_page, '"owner":{"__typename":"User","id":"', '"'
            ),
            "user_pfbid": "",
            "caption": self.decode_all(text.extr(
                photo_page,
                '"message":{"delight_ranges"',
                '"},"message_preferred_body"'
            ).rsplit('],"text":"', 1)[-1]),
            "date": text.parse_timestamp(
                text.extr(photo_page, '\\"publish_time\\":', ',') or
                text.extr(photo_page, '"created_time":', ',')
            ),
            "url": self.decode_all(text.extr(
                photo_page, ',"image":{"uri":"', '","'
            )),
            "next_photo_id": text.extr(
                photo_page,
                '"nextMediaAfterNodeId":{"__typename":"Photo","id":"',
                '"'
            ) or text.extr(
                photo_page,
                '"nextMedia":{"edges":[{"node":{"__typename":"Photo","id":"',
                '"'
            )
        }

        if photo["user_id"].startswith("pfbid"):
            photo["user_pfbid"] = photo["user_id"]
            photo["user_id"] = text.extr(
                photo_page, r'\"content_owner_id_new\":\"', r'\"')

        text.nameext_from_url(photo["url"], photo)

        photo["followups_ids"] = []
        for comment_raw in text.extract_iter(
            photo_page, '{"node":{"id"', '"cursor":null}'
        ):
            if ('"is_author_original_poster":true' in comment_raw and
                    '{"__typename":"Photo","id":"' in comment_raw):
                photo["followups_ids"].append(text.extr(
                    comment_raw,
                    '{"__typename":"Photo","id":"',
                    '"'
                ))

        return photo

    def parse_post_page(self, post_page):
        first_photo_url = text.extr(
            text.extr(
                post_page, '"__isMedia":"Photo"', '"target_group"'
            ), '"url":"', ','
        )

        post = {
            "set_id": text.extr(post_page, '{"mediaset_token":"', '"') or
            text.extr(first_photo_url, 'set=', '"').rsplit("&", 1)[0]
        }

        return post

    def parse_video_page(self, video_page):
        video = {
            "id": text.extr(
                video_page, '\\"video_id\\":\\"', '\\"'
            ),
            "username": self.decode_all(text.extr(
                video_page, '"actors":[{"__typename":"User","name":"', '","'
            )),
            "user_id": text.extr(
                video_page, '"owner":{"__typename":"User","id":"', '"'
            ),
            "date": text.parse_timestamp(text.extr(
                video_page, '\\"publish_time\\":', ','
            )),
            "type": "video"
        }

        if not video["username"]:
            video["username"] = self.decode_all(text.extr(
                video_page,
                '"__typename":"User","id":"' + video["user_id"] + '","name":"',
                '","'
            ))

        first_video_raw = text.extr(
            video_page, '"permalink_url"', '\\/Period>\\u003C\\/MPD>'
        )

        audio = {
            **video,
            "url": self.decode_all(text.extr(
                text.extr(
                    first_video_raw,
                    "AudioChannelConfiguration",
                    "BaseURL>\\u003C"
                ),
                "BaseURL>", "\\u003C\\/"
            )),
            "type": "audio"
        }

        video["urls"] = {}

        for raw_url in text.extract_iter(
            first_video_raw, 'FBQualityLabel=\\"', '\\u003C\\/BaseURL>'
        ):
            resolution = raw_url.split('\\"', 1)[0]
            video["urls"][resolution] = self.decode_all(
                raw_url.split('BaseURL>', 1)[1]
            )

        if not video["urls"]:
            return video, audio

        video["url"] = max(
            video["urls"].items(),
            key=lambda x: text.parse_int(x[0][:-1])
        )[1]

        text.nameext_from_url(video["url"], video)
        audio["filename"] = video["filename"]
        audio["extension"] = "m4a"

        return video, audio

    def photo_page_request_wrapper(self, url, **kwargs):
        LEFT_OFF_TXT = "" if url.endswith("&set=") else (
            "\nYou can use this URL to continue from "
            "where you left off (added \"&setextract\"): "
            "\n" + url + "&setextract"
        )

        res = self.request(url, **kwargs)

        if res.url.startswith(self.root + "/login"):
            raise exception.AuthRequired(
                message=(f"You must be logged in to continue viewing images."
                         f"{LEFT_OFF_TXT}")
            )

        if b'{"__dr":"CometErrorRoot.react"}' in res.content:
            raise exception.AbortExtraction(
                f"You've been temporarily blocked from viewing images.\n"
                f"Please try using a different account, "
                f"using a VPN or waiting before you retry.{LEFT_OFF_TXT}"
            )

        return res

    def extract_set(self, set_data):
        set_id = set_data["set_id"]
        all_photo_ids = [set_data["first_photo_id"]]

        retries = 0
        i = 0

        while i < len(all_photo_ids):
            photo_id = all_photo_ids[i]
            photo_url = f"{self.root}/photo/?fbid={photo_id}&set={set_id}"
            photo_page = self.photo_page_request_wrapper(photo_url).text

            photo = self.parse_photo_page(photo_page)
            photo["num"] = i + 1

            if self.author_followups:
                for followup_id in photo["followups_ids"]:
                    if followup_id not in all_photo_ids:
                        self.log.debug(
                            "Found a followup in comments: %s", followup_id
                        )
                        all_photo_ids.append(followup_id)

            if not photo["url"]:
                if retries < self.fallback_retries and self._interval_429:
                    seconds = self._interval_429()
                    self.log.warning(
                        "Failed to find photo download URL for %s. "
                        "Retrying in %s seconds.", photo_url, seconds,
                    )
                    self.wait(seconds=seconds, reason="429 Too Many Requests")
                    retries += 1
                    continue
                else:
                    self.log.error(
                        "Failed to find photo download URL for " + photo_url +
                        ". Skipping."
                    )
                    retries = 0
            else:
                retries = 0
                photo.update(set_data)
                yield Message.Directory, photo
                yield Message.Url, photo["url"], photo

            if not photo["next_photo_id"]:
                self.log.debug(
                    "Can't find next image in the set. "
                    "Extraction is over."
                )
            elif photo["next_photo_id"] in all_photo_ids:
                if photo["next_photo_id"] != photo["id"]:
                    self.log.debug(
                        "Detected a loop in the set, it's likely finished. "
                        "Extraction is over."
                    )
            else:
                all_photo_ids.append(photo["next_photo_id"])

            i += 1

    @memcache(keyarg=1)
    def _extract_profile_photos_page(self, profile):
        profile_photos_url = f"{self.root}/{profile}/photos_by"

        for _ in range(self.fallback_retries + 1):
            profile_photos_page = self.request(profile_photos_url).text
            if set_id := self._extract_profile_set_id(profile_photos_page):
                break
            self.log.debug("Got empty profile photos page, retrying...")
        else:
            raise exception.AbortExtraction("Failed to extract profile data")

        avatar_page_url = text.extr(
            profile_photos_page, ',"profilePhoto":{"url":"', '"')

        return set_id, avatar_page_url.replace("\\/", "/")

    def _extract_profile_set_id(self, profile_photos_page):
        set_ids_raw = text.extr(
            profile_photos_page, '"pageItems"', '"page_info"'
        )

        set_id = text.extr(
            set_ids_raw, 'set=', '"'
        ).rsplit("&", 1)[0] or text.extr(
            set_ids_raw, '\\/photos\\/', '\\/'
        )

        return set_id


class FacebookSetExtractor(FacebookExtractor):
    """Base class for Facebook Set extractors"""
    subcategory = "set"
    pattern = (
        BASE_PATTERN +
        r"/(?:(?:media/set|photo)/?\?(?:[^&#]+&)*set=([^&#]+)"
        r"[^/?#]*(?<!&setextract)$"
        r"|([^/?#]+/posts/[^/?#]+)"
        r"|photo/\?(?:[^&#]+&)*fbid=([^/?&#]+)&set=([^/?&#]+)&setextract)"
    )
    example = "https://www.facebook.com/media/set/?set=SET_ID"

    def items(self):
        set_id = self.groups[0] or self.groups[3]
        if path := self.groups[1]:
            post_url = self.root + "/" + path
            post_page = self.request(post_url).text
            set_id = self.parse_post_page(post_page)["set_id"]

        set_url = f"{self.root}/media/set/?set={set_id}"
        set_page = self.request(set_url).text
        set_data = self.parse_set_page(set_page)
        if self.groups[2]:
            set_data["first_photo_id"] = self.groups[2]

        return self.extract_set(set_data)


class FacebookPhotoExtractor(FacebookExtractor):
    """Base class for Facebook Photo extractors"""
    subcategory = "photo"
    pattern = (BASE_PATTERN +
               r"/(?:[^/?#]+/photos/[^/?#]+/|photo(?:.php)?/?\?"
               r"(?:[^&#]+&)*fbid=)([^/?&#]+)[^/?#]*(?<!&setextract)$")
    example = "https://www.facebook.com/photo/?fbid=PHOTO_ID"

    def items(self):
        photo_id = self.groups[0]
        photo_url = f"{self.root}/photo/?fbid={photo_id}&set="
        photo_page = self.photo_page_request_wrapper(photo_url).text

        i = 1
        photo = self.parse_photo_page(photo_page)
        photo["num"] = i

        set_url = f"{self.root}/media/set/?set={photo['set_id']}"
        set_page = self.request(set_url).text

        directory = self.parse_set_page(set_page)

        yield Message.Directory, directory
        yield Message.Url, photo["url"], photo

        if self.author_followups:
            for comment_photo_id in photo["followups_ids"]:
                comment_photo = self.parse_photo_page(
                    self.photo_page_request_wrapper(
                        f"{self.root}/photo/?fbid={comment_photo_id}&set="
                    ).text
                )
                i += 1
                comment_photo["num"] = i
                yield Message.Url, comment_photo["url"], comment_photo


class FacebookVideoExtractor(FacebookExtractor):
    """Base class for Facebook Video extractors"""
    subcategory = "video"
    directory_fmt = ("{category}", "{username}", "{subcategory}")
    pattern = BASE_PATTERN + r"/(?:[^/?#]+/videos/|watch/?\?v=)([^/?&#]+)"
    example = "https://www.facebook.com/watch/?v=VIDEO_ID"

    def items(self):
        video_id = self.groups[0]
        video_url = self.root + "/watch/?v=" + video_id
        video_page = self.request(video_url).text

        video, audio = self.parse_video_page(video_page)

        if "url" not in video:
            return

        yield Message.Directory, video

        if self.videos == "ytdl":
            yield Message.Url, "ytdl:" + video_url, video
        elif self.videos:
            yield Message.Url, video["url"], video
            if audio["url"]:
                yield Message.Url, audio["url"], audio


class FacebookPhotosExtractor(FacebookExtractor):
    """Extractor for Facebook Profile Photos"""
    subcategory = "photos"
    pattern = USER_PATTERN + r"/photos(?:_by)?"
    example = "https://www.facebook.com/USERNAME/photos"

    def items(self):
        set_id = self._extract_profile_photos_page(self.groups[0])[0]
        set_url = f"{self.root}/media/set/?set={set_id}"
        set_page = self.request(set_url).text
        set_data = self.parse_set_page(set_page)
        return self.extract_set(set_data)


class FacebookAvatarExtractor(FacebookExtractor):
    """Extractor for Facebook Profile Avatars"""
    subcategory = "avatar"
    pattern = USER_PATTERN + r"/avatar"
    example = "https://www.facebook.com/USERNAME/avatar"

    def items(self):
        avatar_page_url = self._extract_profile_photos_page(self.groups[0])[1]
        avatar_page = self.photo_page_request_wrapper(avatar_page_url).text

        avatar = self.parse_photo_page(avatar_page)
        avatar["count"] = avatar["num"] = 1
        avatar["type"] = "avatar"

        set_url = f"{self.root}/media/set/?set={avatar['set_id']}"
        set_page = self.request(set_url).text
        directory = self.parse_set_page(set_page)

        yield Message.Directory, directory
        yield Message.Url, avatar["url"], avatar


class FacebookUserExtractor(Dispatch, FacebookExtractor):
    """Extractor for Facebook Profiles"""
    pattern = USER_PATTERN + r"/?(?:$|\?|#)"
    example = "https://www.facebook.com/USERNAME"

    def items(self):
        base = f"{self.root}/{self.groups[0]}/"
        return self._dispatch_extractors((
            (FacebookAvatarExtractor, base + "avatar"),
            (FacebookPhotosExtractor, base + "photos"),
        ), ("photos",))
