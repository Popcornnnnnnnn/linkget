"""gallery-dl extension: count only the Instagram photos/videos we save."""
from gallery_dl.extractor import instagram


class LinkgetInstagramPostExtractor(instagram.InstagramPostExtractor):
    def _parse_post(self, post):
        # gallery-dl includes optional music in _files/count, even with audio
        # disabled. Remove only those explicit audio resources before items()
        # assigns the count; never infer completeness from downloaded files.
        expected = len(post.get("carousel_media") or (post,))
        data = super()._parse_post(post)
        files = data.get("_files")
        if not isinstance(files, list) or not all(isinstance(item, dict) for item in files):
            raise self.exc.AbortExtraction("Instagram returned unreadable media information; files have been kept.")
        visual = [item for item in files if not item.get("audio_url")]
        numbers = [item.get("num") for item in visual]
        if (len(visual) != expected or any(type(number) is not int for number in numbers)
                or set(numbers) != set(range(1, expected + 1))):
            raise self.exc.AbortExtraction("Instagram did not expose every photo/video in this post; files have been kept.")
        data["_files"] = visual
        return data
