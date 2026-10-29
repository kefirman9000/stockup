"""Reading photos: titles, keywords, location, colour profile, size, quality, GPS."""
from helpers import ADOBE_ICC, make_photo


def test_reads_xmp_title_keywords_and_place(stockup, batch_dir):
    p = make_photo(batch_dir, "a.jpg", city="Camden", country="USA")
    m = stockup.read_photo(p)[0]
    assert m["title"] == "Sailboats on a calm bay"
    assert m["description"] == "Sailboats anchored on a calm bay at sunset"
    assert m["keywords"][:3] == ["harbor", "boat", "summer"]
    assert (m["city"], m["country"], m["date"]) == ("Camden", "USA", "2026-07-04")


def test_reads_iptc_when_there_is_no_xmp(stockup, batch_dir):
    p = make_photo(batch_dir, "a.jpg", title="Parade", desc="Crowd at the parade", iptc=True)
    m = stockup.read_photo(p)[0]
    assert (m["title"], m["description"], m["date"]) == ("Parade", "Crowd at the parade", "2026-05-12")
    assert len(m["keywords"]) == 10


def test_reads_size_profile_quality_and_gps(stockup, batch_dir):
    info = stockup.read_photo(make_photo(batch_dir, "a.jpg", size=(3000, 2000), quality=75))[0]["info"]
    assert (info["width"], info["height"], info["color"], info["gps"]) == (3000, 2000, "sRGB", False)
    assert abs(info["quality"] - 75) <= 2
    info = stockup.read_photo(make_photo(batch_dir, "b.jpg", icc=ADOBE_ICC, gps=True, progressive=True))[0]["info"]
    assert (info["color"], info["gps"], info["progressive"]) == ("Adobe RGB", True, True)


def test_cmyk_is_recognised(stockup, batch_dir):
    from PIL import Image
    p = f"{batch_dir}/c.jpg"
    Image.new("CMYK", (100, 100)).save(p)
    assert stockup.read_photo(p)[0]["info"]["color"] == "CMYK"


def test_editorial_caption_detection(stockup):
    yes = ["BOSTON, MASSACHUSETTS - APRIL 20, 2026: Runners at the finish",
           "Rome, Italy - 04.09.2026: Tourists at the Colosseum",
           "CAMDEN, MAINE - JULY 4, 2026: Boats leave the harbor"]
    no = ["Sailboats anchored on a calm bay", "Boats - harbor at sunset", "2026 calendar: planning"]
    assert all(stockup.looks_editorial(t) for t in yes)
    assert not any(stockup.looks_editorial(t) for t in no)


def test_shorten_cuts_at_a_word_and_never_ends_on_a_small_word(stockup):
    t = "Golden evening light over rolling desert dunes, warm sand ridges and a purple sky"
    out = stockup.shorten(t, 70)
    assert len(out) <= 70 and t.startswith(out)
    assert out.split()[-1].lower() not in {"a", "and", "the", "of"}
    assert stockup.shorten("Short title", 70) == "Short title"


def test_safe_names(stockup):
    assert stockup.safe_name("Café à Paris – nuit.JPG") == "Cafe_a_Paris_nuit.jpg"
    assert len(stockup.safe_name("x" * 60 + ".jpg", 30)) <= 30
