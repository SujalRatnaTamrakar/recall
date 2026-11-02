#!/usr/bin/env python3
"""Build the deterministic demo fixtures (hand-authored, scripted data).

    PYTHONPATH=src python fixtures/build_fixtures.py

Writes memory.json into fixtures/gta_rp_demo/ and fixtures/valorant_demo/.
The story data below is fictional and internally consistent; the query
engine answers from this data (no hardcoded answers anywhere).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from tubelive.schema import (ChatMessage, Entity, GameEvent, Record,
                             SceneSegment, StreamEvent, TranscriptSegment,
                             TubeTrack, VideoMeta)
from tubelive.store import StreamMemory

JP = "Jean Paul"
MARTY = "Marty"


def ts(t0, t1, speaker, text, entities=()):
    return TranscriptSegment(t_start=t0, t_end=t1, speaker=speaker, text=text,
                             entities=list(entities), confidence=0.92)


def scene(t0, t1, location, activity, caption, entities=()):
    return SceneSegment(t_start=t0, t_end=t1, location=location,
                        activity=activity, caption=caption, text=caption,
                        entities=list(entities), confidence=0.9)


def chat(t, user, text, entities=(), emotes=()):
    return ChatMessage(t_start=t, t_end=t, user=user, text=text,
                       entities=list(entities), emotes=list(emotes),
                       confidence=1.0)


def sevent(t, etype, text, payload=None):
    return StreamEvent(t_start=t, t_end=t, type=etype, text=text,
                       payload=payload or {}, confidence=1.0)


def gevent(t, gtype, text, payload=None, entities=()):
    return GameEvent(t_start=t, t_end=t, type=gtype, text=text,
                     payload=payload or {}, entities=list(entities),
                     confidence=0.95)


# ---------------------------------------------------------------------------
# GTA RP demo — a fictional 40-minute stream (2400 s)
# ---------------------------------------------------------------------------

def build_gta() -> StreamMemory:
    records: list[Record] = []

    # --- Scenes (video layer) ---------------------------------------------
    records += [
        scene(0, 300, "Legion Square", "hanging_out",
              "Jean Paul hangs out at Legion Square and tells Marty the plan "
              "to rob Fleeca Bank", [JP, MARTY]),
        scene(300, 480, "Vinewood Boulevard", "driving_to_bank",
              "Jean Paul drives to Fleeca Bank and scopes the cameras and "
              "the guard", [JP]),
        scene(480, 840, "Fleeca Bank", "bank_robbery",
              "Jean Paul robs Fleeca Bank: thermite on the vault door and "
              "the cash trays loaded into bags", [JP]),
        scene(840, 900, "Getaway Alley", "escape",
              "Jean Paul escapes from the bank through the back alley to "
              "the getaway car", [JP]),
        scene(900, 1200, "Los Santos Freeway", "police_chase",
              "Police chase: three police units pursue Jean Paul and the "
              "wanted level rises to 4 stars", [JP]),
        scene(1200, 1380, "Vespucci Canals", "stashing_car",
              "Jean Paul stashes the getaway car at the canals and lays low "
              "as the wanted level drops", [JP]),
        scene(1380, 1620, "Alta Apartments", "meeting_marty",
              "Jean Paul meets Marty at the apartments and hands over "
              "Marty's split of the money", [JP, MARTY]),
        scene(1620, 1800, "Del Perro", "traffic_stop",
              "Jean Paul is pulled over by police and talks his way out "
              "with a hospital story, leaving with just a warning", [JP]),
        scene(1800, 2100, "Hideout Garage", "laying_low",
              "Jean Paul lays low at the hideout garage, counts the money "
              "and fixes the spare car", [JP]),
        scene(2100, 2400, "Hideout Garage", "planning_next",
              "Jean Paul plans the next move at the hideout and says "
              "Paleto Bay bank is next after laying low", [JP]),
    ]

    # --- Transcript ---------------------------------------------------------
    records += [
        ts(70, 78, JP, "Morning everyone. Today is the day, no more small stuff.", [JP]),
        ts(130, 142, JP, "Okay listen, today we do it clean. We hit the Fleeca on Vinewood, in and out in four minutes, nobody gets hurt.", [JP]),
        ts(210, 218, MARTY, "I got the thermite and the getaway car. You sure about this, JP?", [MARTY, JP]),
        ts(260, 266, JP, "I'm sure. You wait two blocks away, engine running.", [JP]),
        ts(360, 368, JP, "Driving to the bank now. Everyone stay off the radio unless it's urgent.", [JP]),
        ts(430, 438, JP, "That's it on the left. Fleeca Bank. Cameras on the corner, one guard.", [JP]),
        ts(500, 506, JP, "We're in. Thermite on the vault door, move, move!", [JP]),
        ts(560, 568, JP, "Tellers stay calm, this is a bank robbery, nobody be a hero.", [JP]),
        ts(640, 648, JP, "Vault's open. Grab the cash trays, leave the dye packs.", [JP]),
        ts(760, 766, JP, "Two minutes. Bags are loaded, let's go, let's go.", [JP]),
        ts(850, 856, JP, "We're out! Go go go, take the alley behind the bank!", [JP]),
        ts(920, 930, JP, "Cops are behind us, that's three units. This is a full police chase now.", [JP]),
        ts(1020, 1028, JP, "Don't shoot, just drive. Lose them in the canals.", [JP]),
        ts(1130, 1138, JP, "One unit left... he's pitting me, hold on!", [JP]),
        ts(1210, 1220, JP, "We lost them. Wanted level is dropping. Ditch the car at the canals.", [JP]),
        ts(1330, 1338, JP, "Car's stashed. We lay low here for a bit.", [JP]),
        ts(1450, 1458, MARTY, "You actually did it. I heard the sirens from the apartments.", [MARTY, JP]),
        ts(1520, 1528, JP, "Split is in your glove box, Marty. Don't spend it loud.", [JP, MARTY]),
        ts(1680, 1688, "Police Officer", "Sir, license and registration. You were doing sixty in a thirty.", []),
        ts(1730, 1740, JP, "Officer, my cousin is in hospital, I was rushing to see him. I'm so sorry.", [JP]),
        ts(1780, 1788, "Police Officer", "Slow it down. This is your lucky day, it's just a warning.", []),
        ts(1900, 1908, JP, "He bought it! Talked my way out of that stop, chat.", [JP]),
        ts(2050, 2060, JP, "We count the money, fix the spare car, and keep our heads down tonight.", [JP]),
        ts(2250, 2262, JP, "We lay low tonight, let the heat die. After this we hit Paleto, I swear - Paleto Bay bank is next.", [JP]),
        ts(2330, 2340, JP, "Paleto crew needs a driver and a second thermite. Start asking around, quietly.", [JP]),
    ]

    # --- Game events (HUD state) ---------------------------------------------
    records += [
        gevent(840, "wanted_level", "Wanted level rises to 1 star as Jean Paul leaves the bank", {"wanted_level": 1}, [JP]),
        gevent(905, "wanted_level", "Wanted level rises to 3 stars as more units join the chase", {"wanted_level": 3}, [JP]),
        gevent(960, "money", "Money secured from Fleeca Bank: $84,000 in the bags", {"money": 84000}, [JP]),
        gevent(1000, "wanted_level", "Wanted level rises to 4 stars during the police chase", {"wanted_level": 4}, [JP]),
        gevent(1210, "wanted_level", "Wanted level drops to 2 stars after the chase ends", {"wanted_level": 2}, [JP]),
        gevent(1390, "wanted_level", "Wanted level cleared: no stars", {"wanted_level": 0}, [JP]),
    ]

    # --- Stream events ---------------------------------------------------------
    records += [
        sevent(95, "follow", "follow from luna_wolf"),
        sevent(200, "sub", "new sub from rex_plays (Tier 1)"),
        sevent(700, "follow", "follow from quietstorm"),
        sevent(870, "clip_created", "clip created by a viewer: 'the bank escape'"),
        sevent(950, "sub", "resub from mika_ttv (6 months)"),
        sevent(1250, "hype_train", "hype train started after the chase", {"level": 1}),
        sevent(1310, "sub", "gifted sub from bigdonor to chat"),
        sevent(1355, "hype_train", "hype train reached level 3", {"level": 3}),
        sevent(1500, "follow", "follow from novo_"),
        sevent(1700, "sub", "new sub from carla_gg (Tier 1)"),
        sevent(1980, "ad_break", "ad break (60s)", {"duration_s": 60}),
        sevent(2050, "sub", "resub from jules_ (3 months)"),
        sevent(2300, "sub", "new sub from pixelpedro (Tier 2)"),
    ]

    # --- Chat (key reactions + filler; spikes at escape ~14:00 and chase end ~20:00)
    chats = [
        (45, "luna_wolf", "morning JP!"), (120, "rex_plays", "LETS GOOO"),
        (150, "mika_ttv", "a BANK?? Jean Paul is really doing it"),
        (230, "quietstorm", "Marty is the worst getaway driver btw", [MARTY]),
        (390, "novo_", "scoping the bank, professional criminal arc"),
        (470, "carla_gg", "my heart is racing and he is not even inside yet"),
        (510, "rex_plays", "THERMITE OUT, its happening!!", [JP]),
        (570, "luna_wolf", "JP robbing the bank in broad daylight lol", [JP]),
        (650, "mika_ttv", "grab the cash trays!!"),
        (700, "jules_", "vault is OPEN lets gooo"),
        (770, "bigdonor", "bags loaded, RUN"),
        (830, "pixelpedro", "cops are coming i can hear sirens"),
    ]
    for c in chats:
        records.append(chat(c[0], c[1], c[2],
                            list(c[3]) if len(c) > 3 else []))
    # escape spike (bucket 840-900)
    for i, (u, x) in enumerate([
            ("rex_plays", "GO GO GO"), ("mika_ttv", "HE'S OUT!!"),
            ("luna_wolf", "THE ESCAPE"), ("jules_", "CLIP IT"),
            ("carla_gg", "ALLEY ALLEY ALLEY"), ("novo_", "getaway car NOW"),
            ("bigdonor", "JP DID IT"), ("pixelpedro", "sirens everywhere"),
            ("quietstorm", "that was clean??"), ("rex_plays", "bank robbed, Jean Paul is gone", )]):
        records.append(chat(842 + i * 5, u, x, [JP], ["PogChamp"]))
    records += [
        chat(930, "mika_ttv", "3 units on him, this chase is crazy", [JP]),
        chat(990, "rex_plays", "4 STARS"), chat(1050, "jules_", "canals strat, trust"),
        chat(1140, "carla_gg", "THE PIT, he saved it!!"),
    ]
    # chase-end spike (bucket 1200-1260)
    for i, (u, x) in enumerate([
            ("luna_wolf", "LOST THEM"), ("rex_plays", "CHASE OVER"),
            ("mika_ttv", "Jean Paul actually escaped the cops", ),
            ("jules_", "W chase"), ("bigdonor", "hype train incoming"),
            ("carla_gg", "canals OP"), ("novo_", "stash the car stash the car"),
            ("pixelpedro", "GG JP"), ("quietstorm", "wanted level dropping"),
            ("rex_plays", "best chase of the week"), ("mika_ttv", "PogChamp", )]):
        records.append(chat(1204 + i * 5, u, x, [JP], ["PogChamp"]))
    records += [
        chat(1420, "novo_", "Marty appearance!!", [MARTY]),
        chat(1470, "luna_wolf", "Marty heard the sirens from the apartments lol", [MARTY]),
        chat(1550, "jules_", "dont spend it loud - Marty will spend it loud", [MARTY]),
        chat(1650, "carla_gg", "oh no, pulled over"),
        chat(1705, "rex_plays", "the hospital story, classic JP", [JP]),
        chat(1795, "mika_ttv", "HE TALKED HIS WAY OUT"),
        chat(1915, "bigdonor", "that officer bought the whole story"),
        chat(2070, "quietstorm", "counting money arc, quiet night"),
        chat(2210, "novo_", "whats next for JP tho?", [JP]),
        chat(2265, "luna_wolf", "PALETO?? Jean Paul said Paleto is next!!", [JP]),
        chat(2280, "rex_plays", "Paleto Bay bank next, calling it now"),
        chat(2350, "mika_ttv", "second thermite for Paleto, this arc is cinema"),
    ]

    tracks = [
        TubeTrack(track_id=1, label="person", first_t=0, last_t=2400,
                  entity=JP, zones_visited=[
                      "Legion Square", "Vinewood Boulevard", "Fleeca Bank",
                      "Getaway Alley", "Los Santos Freeway", "Vespucci Canals",
                      "Alta Apartments", "Del Perro", "Hideout Garage"]),
        TubeTrack(track_id=2, label="person", first_t=0, last_t=1620,
                  entity=MARTY, zones_visited=["Legion Square", "Alta Apartments"]),
        TubeTrack(track_id=3, label="police_car", first_t=850, last_t=1800,
                  entity=None, zones_visited=["Fleeca Bank", "Los Santos Freeway",
                                              "Vespucci Canals", "Del Perro"]),
        TubeTrack(track_id=4, label="car", first_t=300, last_t=1380,
                  entity=None, zones_visited=["Vinewood Boulevard", "Fleeca Bank",
                                              "Getaway Alley", "Los Santos Freeway",
                                              "Vespucci Canals"],
                  payload={"role": "getaway car"}),
    ]
    entities = [Entity(name=JP, aliases=["JP", "Jean"]),
                Entity(name=MARTY, aliases=[]),
                Entity(name="Police Officer", aliases=["cop", "officer"],
                       kind="character")]
    return StreamMemory(
        video=VideoMeta(id="gta-rp-demo", title="GTA RP — The Fleeca Job (fictional demo)",
                        duration_s=2400, fps=30, width=1920, height=1080,
                        source="fixtures/gta_rp_demo/stream.mp4 (fictional, scripted)",
                        game="GTA V RP"),
        records=records, tracks=tracks, entities=entities)


# ---------------------------------------------------------------------------
# Valorant demo — one round with a 1v5 clutch (360 s)
# ---------------------------------------------------------------------------

def build_valorant() -> StreamMemory:
    records: list[Record] = [
        scene(0, 60, "Lobby", "queue", "Streamer queues into a ranked match on Ascent", ["Streamer"]),
        scene(60, 270, "Ascent A Site", "round_in_progress",
              "Round 12 on Ascent: streamer's team attacks A site and the round collapses to a 1v5 clutch attempt", ["Streamer"]),
        scene(270, 360, "Ascent A Site", "celebration",
              "Streamer celebrates the 1v5 clutch round win with chat", ["Streamer"]),
        ts(75, 82, "Streamer", "Okay, A site, play slow, use my dash late.", ["Streamer"]),
        ts(155, 163, "Streamer", "I'm last alive, it's a 1v5, I have to clutch this. Quiet, chat.", ["Streamer"]),
        ts(262, 270, "Streamer", "LET'S GO! 1v5 clutch, defused with no time left!", ["Streamer"]),
        gevent(60, "round_start", "Round 12 starts on Ascent", {"round": 12}),
        gevent(85, "alive_count_change", "Teammate down: 4v5", {"allies_alive": 4, "enemies_alive": 5}),
        gevent(110, "alive_count_change", "Teammate down: 3v5", {"allies_alive": 3, "enemies_alive": 5}),
        gevent(132, "alive_count_change", "Teammate down: 2v5", {"allies_alive": 2, "enemies_alive": 5}),
        gevent(150, "alive_count_change", "Streamer is last alive: 1v5 clutch situation", {"allies_alive": 1, "enemies_alive": 5}),
        gevent(165, "kill", "Streamer kills enemy Reyna (headshot)", {"actor_side": "allies", "victim": "Reyna"}),
        gevent(185, "kill", "Streamer kills enemy Sova", {"actor_side": "allies", "victim": "Sova"}),
        gevent(205, "kill", "Streamer kills enemy Killjoy", {"actor_side": "allies", "victim": "Killjoy"}),
        gevent(230, "kill", "Streamer kills enemy Sage", {"actor_side": "allies", "victim": "Sage"}),
        gevent(255, "kill", "Streamer kills enemy Jett — all five down", {"actor_side": "allies", "victim": "Jett"}),
        gevent(258, "alive_count_change", "No enemies remain: 1v0", {"allies_alive": 1, "enemies_alive": 0}),
        gevent(270, "round_end", "Round 12 won by streamer's team", {"result": "win", "side": "allies", "round": 12}),
        sevent(280, "sub", "new sub from clutch_fan (Tier 1)"),
        sevent(285, "clip_created", "clip created by a viewer: 'the 1v5 clutch'"),
        chat(100, "aim_god_fan", "team is throwinggg"),
        chat(152, "valofan", "1v5?? its over"),
        chat(167, "valofan", "ONE DOWN"),
        chat(190, "aim_god_fan", "TWO?? streamer is locked in"),
        chat(210, "mod_mara", "three down, this is actually happening"),
        chat(235, "valofan", "FOUR"),
        chat(258, "aim_god_fan", "ALL FIVE, 1v5 CLUTCH"),
        chat(272, "mod_mara", "CLIPPED. best round of the stream"),
    ]
    tracks = [TubeTrack(track_id=1, label="player", first_t=60, last_t=360,
                        entity="Streamer", zones_visited=["Ascent A Site",
                                                          "Ascent A Main"])]
    entities = [Entity(name="Streamer", aliases=["the streamer"], kind="streamer")]
    return StreamMemory(
        video=VideoMeta(id="valorant-demo", title="Valorant — 1v5 Clutch Round (fictional demo)",
                        duration_s=360, fps=60, width=1920, height=1080,
                        source="fixtures/valorant_demo/stream.mp4 (fictional, scripted)",
                        game="Valorant"),
        records=records, tracks=tracks, entities=entities)


def main() -> None:
    root = Path(__file__).resolve().parent
    gta = build_gta()
    gta.save(root / "gta_rp_demo" / "memory.json")
    print(f"gta_rp_demo: {len(gta.records)} records, {len(gta.tracks)} tracks")
    val = build_valorant()
    val.save(root / "valorant_demo" / "memory.json")
    print(f"valorant_demo: {len(val.records)} records, {len(val.tracks)} tracks")


if __name__ == "__main__":
    main()
