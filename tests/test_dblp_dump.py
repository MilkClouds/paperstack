import gzip
from xml.etree import ElementTree

import polars as pl
import pytest

from paperstack import dblp_build, dblp_dump, dblp_index

DUMP = """<?xml version="1.0" encoding="ISO-8859-1"?>
<!DOCTYPE dblp SYSTEM "dblp.dtd">
<dblp>
<inproceedings key="conf/rss/ChiFDXCBS23" mdate="2023-08-01">
<author>Cheng Chi</author>
<author>Andreas G&ouml;bel 0001</author>
<title>Diffusion Policy: <i>Visuomotor</i> Policy Learning via Action Diffusion.</title>
<year>2023</year>
<booktitle>Robotics: Science and Systems</booktitle>
<ee>https://doi.org/10.15607/RSS.2023.XIX.026</ee>
<crossref>conf/rss/2023</crossref>
<url>db/conf/rss/rss2023.html#ChiFDXCBS23</url>
</inproceedings>
<proceedings key="conf/rss/2023" mdate="2023-08-01">
<editor>Kostas E. Bekris</editor>
<title>Robotics: Science and Systems XIX, Daegu, Republic of Korea, July 10-14, 2023.</title>
<year>2023</year>
<url>db/conf/rss/rss2023.html</url>
</proceedings>
<inproceedings key="conf/cvpr/Part26" mdate="2026-07-01">
<author>A. Author</author>
<title>A Paper in a Multi-Part Volume</title>
<year>2026</year>
<booktitle>CVPR</booktitle>
<url>db/conf/cvpr/cvpr2026-2.html#Part26</url>
</inproceedings>
<inproceedings key="conf/cvpr/Workshop26" mdate="2026-07-01">
<author>W. Author</author>
<title>A Workshop Paper</title>
<year>2026</year>
<booktitle>CVPR Workshops</booktitle>
<url>db/conf/cvpr/cvpr2026w.html#Workshop26</url>
</inproceedings>
<article key="journals/jmlr/Jour26" mdate="2026-07-01">
<author>J. Author</author>
<title>A Journal Article</title>
<journal>J. Mach. Learn. Res.</journal>
<volume>27</volume>
<year>2026</year>
<url>db/journals/jmlr/jmlr27.html#Jour26</url>
</article>
</dblp>
"""


def _dump(tmp_path):
    path = tmp_path / "dblp.xml.gz"
    with gzip.open(path, "wt", encoding="iso-8859-1") as handle:
        handle.write(DUMP)
    return path


def test_dump_maps_catalog_tocs_and_skips_other_pages(tmp_path):
    grouped = dblp_dump.load(_dump(tmp_path), ["rss", "cvpr", "jmlr"])

    assert sorted(grouped) == [("cvpr", 2026), ("jmlr", 2026), ("rss", 2023)]
    assert len(grouped[("rss", 2023)]) == 2  # paper and its proceedings record
    assert "Workshop" not in "".join(grouped[("cvpr", 2026)])


def test_dump_records_become_index_rows(tmp_path):
    grouped = dblp_dump.load(_dump(tmp_path), ["rss", "jmlr"])
    rows = {row[2]: row for row in (dblp_index._row("", b) for entries in grouped.values() for b in entries)}

    paper = rows["DBLP:conf/rss/ChiFDXCBS23"]
    assert paper[1] == "Diffusion Policy: Visuomotor Policy Learning via Action Diffusion"
    assert paper[3] == "Cheng Chi and Andreas Göbel"
    assert paper[4] == "Robotics: Science and Systems XIX, Daegu, Republic of Korea, July 10-14, 2023"
    assert paper[5:8] == (2023, "inproceedings", "10.15607/RSS.2023.XIX.026")
    article = rows["DBLP:journals/jmlr/Jour26"]
    assert article[4] == "J. Mach. Learn. Res." and article[6] == "article"


def test_builder_writes_every_catalog_record_from_the_dump(tmp_path):
    output = tmp_path / "index.parquet"

    result = dblp_build.build(
        output, snapshot="2026.11", selected_venues=["rss"], minimum_records=1, dump=_dump(tmp_path)
    )

    assert result["refreshed"] == {"rss": {"2023": 2}}
    assert pl.read_parquet(output)["dblp_key"].to_list() == ["DBLP:conf/rss/2023", "DBLP:conf/rss/ChiFDXCBS23"]


def test_truncated_dump_is_rejected(tmp_path):
    path = tmp_path / "dblp.xml.gz"
    with gzip.open(path, "wt", encoding="iso-8859-1") as handle:
        handle.write(DUMP[: DUMP.index("<article")])

    with pytest.raises(ElementTree.ParseError):
        dblp_dump.load(path, ["rss"])
