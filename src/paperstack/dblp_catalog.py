"""Repository-owned DBLP venue catalog."""

from __future__ import annotations

from typing import Any

CURRENT_YEAR = 2026

VENUES: dict[str, dict[str, Any]] = {
    # ML / AI
    "neurips": {"dir": "nips", "toc": "nips", "search_venue": "NeurIPS", "start": 2020},
    "nips": {"dir": "nips", "start": 2000, "end": 2019},
    "icml": {"dir": "icml", "start": 2010},
    "iclr": {"dir": "iclr", "start": 2013},
    "aaai": {"dir": "aaai", "start": 2010},
    "ijcai": {"dir": "ijcai", "years": [2011, 2013, 2015, *range(2016, CURRENT_YEAR + 1)]},
    "aistats": {"dir": "aistats", "start": 2013},
    "uai": {"dir": "uai", "start": 2010},
    "colt": {"dir": "colt", "start": 2010},
    "mlsys": {"dir": "mlsys", "start": 2019},
    # Vision
    "cvpr": {"dir": "cvpr", "start": 2010},
    "iccv": {"dir": "iccv", "start": 2011, "step": 2},
    "eccv": {"dir": "eccv", "start": 2010, "step": 2},
    "bmvc": {"dir": "bmvc", "start": 2015},
    "accv": {"dir": "accv", "start": 2010, "step": 2},
    "miccai": {"dir": "miccai", "start": 2015},
    # NLP / IR
    "acl": {"dir": "acl", "start": 2010, "extra_tocs": ["f"]},
    "emnlp": {"dir": "emnlp", "start": 2010, "extra_tocs": ["f"]},
    "naacl": {
        "dir": "naacl",
        "years": [2010, 2012, 2013, 2015, 2016, 2018, 2019, 2021, 2022, 2024, 2025],
        "extra_tocs": ["f"],
    },
    "eacl": {"dir": "eacl", "years": [2012, 2014, 2017, 2021, 2023, 2024]},
    "coling": {"dir": "coling", "start": 2010, "step": 2},
    "sigir": {"dir": "sigir", "start": 2015},
    "wsdm": {"dir": "wsdm", "start": 2015},
    "cikm": {"dir": "cikm", "start": 2015},
    "www": {"dir": "www", "start": 2015},
    # Systems / data / HCI
    "kdd": {"dir": "kdd", "start": 2010},
    "chi": {"dir": "chi", "start": 2015},
    "sigmod": {"dir": "sigmod", "start": 2015, "suffixes": ["", "c"]},
    "recsys": {"dir": "recsys", "start": 2015},
    # Audio / speech
    "icassp": {"dir": "icassp", "start": 2015},
    "interspeech": {"dir": "interspeech", "start": 2015},
    # Theory
    "stoc": {"dir": "stoc", "start": 2015},
    "soda": {"dir": "soda", "start": 2015},
    # Robotics
    "corl": {"dir": "corl", "start": 2017},
    "rss": {"dir": "rss", "start": 2015},
    "iros": {"dir": "iros", "start": 2015},
    "icra": {"dir": "icra", "start": 2015},
    # Journals
    "tacl": {"dir": "tacl", "start": 2013, "type": "journals", "vol_start": {"year": 2013, "vol": 1}},
    "jmlr": {"dir": "jmlr", "start": 2010, "type": "journals", "vol_start": {"year": 2000, "vol": 1}},
}


def years(venue: str) -> list[int]:
    definition = VENUES[venue]
    if explicit := definition.get("years"):
        return list(explicit)
    return list(
        range(
            definition["start"],
            definition.get("end", CURRENT_YEAR) + 1,
            definition.get("step", 1),
        )
    )


def toc_query(venue: str, year: int, suffix: str = "") -> str:
    definition = VENUES[venue]
    directory = definition["dir"]
    if definition.get("type") == "journals":
        start = definition.get("vol_start")
        volume = start["vol"] + year - start["year"] if start else year
        return f"toc:db/journals/{directory}/{directory}{volume}.bht:"
    stem = definition.get("toc", venue)
    return f"toc:db/conf/{directory}/{stem}{year}{suffix}.bht:"
