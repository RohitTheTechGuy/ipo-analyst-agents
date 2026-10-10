"""Sentiment agent at seam B: fake search + scripted LLM, everything inside runs for real."""
from datetime import date

from ipo_agents.adapters import SearchHit
from ipo_agents.analysis import analyse_rhp
from ipo_agents.budget import BudgetMeter
from ipo_agents.contracts import ClaimedFact, Fact, OfferObjects
from ipo_agents.sentiment import ClaimedNumber, SentimentPoints, StanceTag
from ipo_agents.runner import run_daily
from tests.fakes import FakeExchange, FakeSearch, ScriptedLLM, make_ipo

OFFER = OfferObjects(issue_size_cr=Fact(
    value=1250.0, unit="₹ crore", page=2, verbatim_quote="Total issue size of up to Rs. 1,250.00 crore"))


def test_without_a_search_adapter_sentiment_is_unavailable_and_the_run_completes(tiny_rhp, adapters, config):
    adapters.llm = ScriptedLLM({OfferObjects: OFFER}, models=config.models)

    report = analyse_rhp(make_ipo(), tiny_rhp, adapters, config)

    assert report.status == "complete"
    assert report.sentiment.available is False
    assert "sentiment unavailable" in report.sentiment.note
    assert report.offer.issue_size_cr.value == 1250.0


def test_a_failing_search_makes_sentiment_unavailable_and_the_run_completes(tiny_rhp, adapters, config):
    adapters.search = FakeSearch(error=RuntimeError("search provider down"))
    adapters.llm = llm = sentiment_llm(config, {})

    report = analyse_rhp(make_ipo(), tiny_rhp, adapters, config)

    assert report.status == "complete"
    assert report.sentiment.available is False
    assert "sentiment unavailable" in report.sentiment.note
    assert [c["task"] for c in llm.calls] == ["offer_objects"]


def hit(slug, published, title=None, source="Example News", content=None):
    return SearchHit(url=f"https://news.example/{slug}", title=title or f"Acme Ltd IPO: {slug}",
                     source_name=source, published=published, content=content or f"Article body for {slug}.")


def tagger(stances, claims=None):
    """Scripts the per-source stance call: answers by the article URL found in the prompt."""
    claims = claims or {}

    def tag(user):
        [slug] = [s for s in stances if f"https://news.example/{s}\n" in user]
        return StanceTag(stance=stances[slug], key_reasons=[f"reason from {slug}"], claimed_numbers=claims.get(slug, []))
    return tag


def sentiment_llm(config, stances, claims=None):
    return ScriptedLLM({
        OfferObjects: OFFER,
        StanceTag: tagger(stances, claims),
        SentimentPoints: SentimentPoints(bull_points=["strong order book"], bear_points=["rich valuation"]),
    }, models=config.models)


def test_stance_tally_and_points_come_from_the_fixture_articles(tiny_rhp, adapters, config):
    adapters.search = FakeSearch({
        "Acme Ltd IPO review": [hit("a", date(2026, 10, 8)), hit("b", date(2026, 10, 9))],
        "Acme Ltd IPO subscribe or avoid": [hit("c", date(2026, 10, 10)), hit("a", date(2026, 10, 8))],
        "Acme Ltd IPO broker note": [hit("d", date(2026, 10, 11))],
    })
    adapters.llm = llm = sentiment_llm(config, {"a": "subscribe", "b": "avoid", "c": "subscribe", "d": "listing_gains_only"})

    report = analyse_rhp(make_ipo(), tiny_rhp, adapters, config)

    s = report.sentiment
    assert s.available is True
    # a, b, c, d once each (a appears in two queries): 2 subscribe, 1 avoid, 1 listing gains.
    assert [src.url.rsplit("/", 1)[1] for src in s.sources] == ["a", "b", "c", "d"]
    assert s.tally == {"subscribe": 2, "listing_gains_only": 1, "avoid": 1, "neutral": 0}
    assert s.bull_points == ["strong order book"] and s.bear_points == ["rich valuation"]
    assert [c["task"] for c in llm.calls].count("sentiment_stance_tag") == 4
    [agg] = [c for c in llm.calls if c["task"] == "sentiment_agent"]
    assert "reason from b" in agg["user"] and "avoid" in agg["user"]
    assert [c["model"] for c in llm.calls if c["task"].startswith("sentiment")] == ["groq"] * 4 + ["luna"]


def test_articles_from_before_the_rhp_date_are_excluded(tiny_rhp, adapters, config):
    # No RHP date is known, so the cut-off is the open date (13 Oct) minus 7 days = 6 Oct; "after" is strict.
    adapters.search = search = FakeSearch({"Acme Ltd IPO review": [
        hit("old", date(2026, 10, 5)), hit("on-cutoff", date(2026, 10, 6)), hit("undated", None),
        hit("fresh", date(2026, 10, 7)),
    ]})
    adapters.llm = sentiment_llm(config, {"old": "avoid", "on-cutoff": "avoid", "undated": "avoid", "fresh": "subscribe"})

    report = analyse_rhp(make_ipo(), tiny_rhp, adapters, config)

    assert [s.url for s in report.sentiment.sources] == ["https://news.example/fresh"]
    assert report.sentiment.tally == {"subscribe": 1, "listing_gains_only": 0, "avoid": 0, "neutral": 0}
    assert {q["published_after"] for q in search.queries} == {date(2026, 10, 6)}
    assert "open date minus 7 days" in report.sentiment.note


def test_third_party_numbers_are_claimed_facts_carrying_their_source(tiny_rhp, adapters, config):
    adapters.search = FakeSearch({"Acme Ltd IPO broker note": [
        hit("note", date(2026, 10, 9), source="Broker Securities"),
        hit("blog", date(2026, 10, 10), source="IPO Blog"),
    ]})
    adapters.llm = sentiment_llm(config, {"note": "subscribe", "blog": "neutral"}, claims={
        "note": [ClaimedNumber(label="post-issue P/E", value=32.5, unit="x")],
        "blog": [ClaimedNumber(label="GMP", value=18.0, unit="₹")],
    })

    report = analyse_rhp(make_ipo(), tiny_rhp, adapters, config)

    assert report.sentiment.claimed_numbers == [
        ClaimedFact(label="post-issue P/E", value=32.5, unit="x", source_url="https://news.example/note",
                    source_name="Broker Securities", as_of=date(2026, 10, 9)),
        ClaimedFact(label="GMP", value=18.0, unit="₹", source_url="https://news.example/blog",
                    source_name="IPO Blog", as_of=date(2026, 10, 10)),
    ]
    # The RHP-verified offer facts are untouched by what third parties claim.
    assert report.offer == OFFER


def test_sponsored_content_and_denylisted_domains_are_dropped_before_tagging(tiny_rhp, adapters, config):
    config = config.model_copy(update={"sentiment": config.sentiment.model_copy(
        update={"denylist_domains": ["ipo-tips.example"]})})
    spam_site = SearchHit(url="https://www.ipo-tips.example/acme", title="Acme Ltd IPO: sure-shot gains",
                          source_name="IPO Tips", published=date(2026, 10, 8), content="Apply now!")
    adapters.search = FakeSearch({"Acme Ltd IPO review": [
        hit("paid", date(2026, 10, 8), title="Sponsored: Acme Ltd IPO is a must-buy"),
        hit("advert", date(2026, 10, 8), content="This advertorial is brought to you by Acme Ltd."),
        hit("partner", date(2026, 10, 8), content="In paid partnership with Acme Ltd. Subscribe!"),
        spam_site,
        hit("honest", date(2026, 10, 9)),
    ]})
    adapters.llm = llm = sentiment_llm(config, {"honest": "avoid"})

    report = analyse_rhp(make_ipo(), tiny_rhp, adapters, config)

    assert [s.url for s in report.sentiment.sources] == ["https://news.example/honest"]
    assert report.sentiment.tally == {"subscribe": 0, "listing_gains_only": 0, "avoid": 1, "neutral": 0}
    assert [c["task"] for c in llm.calls].count("sentiment_stance_tag") == 1


TEN_ARTICLES = {"Acme Ltd IPO review": [hit(f"n{i}", date(2026, 10, 8)) for i in range(10)]}


def test_at_most_eight_sources_are_read(tiny_rhp, adapters, config):
    adapters.search = FakeSearch(TEN_ARTICLES)
    adapters.llm = llm = sentiment_llm(config, {f"n{i}": "neutral" for i in range(10)})

    report = analyse_rhp(make_ipo(), tiny_rhp, adapters, config)

    assert [s.url for s in report.sentiment.sources] == [f"https://news.example/n{i}" for i in range(8)]
    assert [c["task"] for c in llm.calls].count("sentiment_stance_tag") == 8


def test_degraded_run_reads_at_most_four_sources(tiny_rhp, adapters, config, monkeypatch):
    # Ticket 10 adds meter.degraded (spend >= 70% of the cap); until it lands, stub it on the real meter.
    # TODO(ticket 10): drive this with persisted prior spend >= 70% of the cap instead of the stub.
    monkeypatch.setattr(BudgetMeter, "degraded", True, raising=False)
    adapters.search = FakeSearch(TEN_ARTICLES)
    adapters.llm = llm = sentiment_llm(config, {f"n{i}": "neutral" for i in range(10)})

    report = analyse_rhp(make_ipo(), tiny_rhp, adapters, config)

    assert [s.url for s in report.sentiment.sources] == [f"https://news.example/n{i}" for i in range(4)]
    assert report.sentiment.tally["neutral"] == 4
    assert [c["task"] for c in llm.calls].count("sentiment_stance_tag") == 4


def test_telegram_summary_carries_the_stance_tally(adapters, config):
    adapters.exchange = FakeExchange([make_ipo()])
    adapters.search = FakeSearch({"Acme Ltd IPO review": [hit(s, date(2026, 10, 8)) for s in "abcdefghi"]})
    stances = dict(zip("abcdefghi", ["subscribe"] * 6 + ["avoid"] * 2 + ["neutral"]))
    adapters.llm = sentiment_llm(config, stances)

    run_daily(date(2026, 10, 13), adapters, config)

    # 9 articles, first 8 read: a-f subscribe, g-h avoid; i (neutral) is past the cap of 8.
    [msg] = adapters.notifier.messages
    assert "Sentiment: 6 subscribe / 2 avoid" in msg.splitlines()


def test_telegram_summary_says_when_sentiment_is_unavailable(adapters, config):
    adapters.exchange = FakeExchange([make_ipo()])
    adapters.llm = sentiment_llm(config, {})

    run_daily(date(2026, 10, 13), adapters, config)

    [msg] = adapters.notifier.messages
    assert "Sentiment: unavailable" in msg.splitlines()
