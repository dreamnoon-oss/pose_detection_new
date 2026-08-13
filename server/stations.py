"""Station registry, pinyin mapping, annotation resolution and detection configs.

This module is the single source of truth for the line/station hierarchy used
by the web UI. It reads the station list from the bundled CSV, computes a
lowercase-pinyin ``station_key`` for every station (used for file naming and
binding), and knows how to resolve a station to its annotation JSON file on
disk (supporting both the new ``regions_{line}_{key}.json`` naming and the
legacy ``regions_{key}.json`` naming used by the existing seven stations).
"""

import os
import csv

from src.config import DATA_DIR

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LINES = ["3号线", "4号线", "7号线", "15号线"]

_CSV_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "上海地铁3_4_7_15号线站点信息.csv")

# ---------------------------------------------------------------------------
# Pinyin map: every unique station name → lowercase pinyin key (no spaces).
# ---------------------------------------------------------------------------

PINYIN = {
    # 3号线
    "江杨北路": "jiangyangbeilu",
    "铁力路": "tielilu",
    "友谊路": "youyilu",
    "宝杨路": "baoyanglu",
    "水产路": "shuichanlu",
    "淞滨路": "songbinlu",
    "张华浜": "zhanghuabang",
    "淞发路": "songfalu",
    "长江南路": "changjiangnanlu",
    "殷高西路": "yingaoxilu",
    "江湾镇": "jiangwanzhen",
    "大柏树": "daboshu",
    "赤峰路": "chifenglu",
    "虹口足球场": "hongkouzuqiuchang",
    "东宝兴路": "dongbaoxinglu",
    "宝山路": "baoshanlu",
    "上海火车站": "shanghaihuochezhan",
    "中潭路": "zhongtanlu",
    "镇坪路": "zhenpinglu",
    "曹杨路": "caoyanglu",
    "金沙江路": "jinshajianglu",
    "中山公园": "zhongshangongyuan",
    "延安西路": "yananxilu",
    "虹桥路": "hongqiaolu",
    "宜山路": "yishanlu",
    "漕溪路": "caoxilu",
    "龙漕路": "longcaolu",
    "石龙路": "shilonglu",
    "上海南站": "shanghainanzhan",
    # 4号线
    "上海体育馆": "shanghaitiyuguan",
    "上海体育场": "shanghaitiyuchang",
    "东安路": "donganlu",
    "大木桥路": "damuqiaolu",
    "鲁班路": "lubanlu",
    "西藏南路": "xizangnanlu",
    "南浦大桥": "nanpudaqiao",
    "塘桥": "tangqiao",
    "蓝村路": "lancunlu",
    "向城路": "xiangchenglu",
    "世纪大道": "shijidadao",
    "浦东大道": "pudongdadao",
    "杨树浦路": "yangshupulu",
    "大连路": "dalianlu",
    "临平路": "linpinglu",
    "海伦路": "hailunlu",
    # 7号线
    "美兰湖": "meilanhu",
    "罗南新村": "luonanxincun",
    "潘广路": "panguanglu",
    "刘行": "liuxing",
    "顾村公园": "gucungongyuan",
    "祁华路": "qihualu",
    "上海大学": "shanghaidaxue",
    "南陈路": "nanchenlu",
    "上大路": "shangdalu",
    "场中路": "changzhonglu",
    "大场镇": "dachangzhen",
    "行知路": "xingzhilu",
    "大华三路": "dahuasanlu",
    "新村路": "xincunlu",
    "岚皋路": "langaolu",
    "长寿路": "changshoulu",
    "昌平路": "changpinglu",
    "静安寺": "jingansi",
    "常熟路": "changshulu",
    "肇嘉浜路": "zhaojiabanglu",
    "龙华中路": "longhuazhonglu",
    "后滩": "houtan",
    "长清路": "changqinglu",
    "耀华路": "yaohualu",
    "云台路": "yuntailu",
    "高科西路": "gaokexilu",
    "杨高南路": "yanggaonanlu",
    "锦绣路": "jinxiulu",
    "芳华路": "fanghualu",
    "龙阳路": "longyanglu",
    "花木路": "huamulu",
    # 15号线
    "紫竹高新区": "zizhugaoxinqu",
    "永德路": "yongdelu",
    "元江路": "yuanjianglu",
    "双柏路": "shuangbailu",
    "曙建路": "shujianlu",
    "景西路": "jingxilu",
    "虹梅南路": "hongmeinanlu",
    "景洪路": "jinghonglu",
    "朱梅路": "zhumeilu",
    "罗秀路": "luoxiulu",
    "华东理工大学": "huadongligongdaxue",
    "桂林公园": "guilingongyuan",
    "桂林路": "guilinlu",
    "吴中路": "wuzhonglu",
    "姚虹路": "yaohonglu",
    "红宝石路": "hongbaoshilu",
    "娄山关路": "loushanguanlu",
    "长风公园": "changfenggongyuan",
    "大渡河路": "daduhelu",
    "梅岭北路": "meilingbeilu",
    "铜川路": "tongchuanlu",
    "上海西站": "shanghaixizhan",
    "武威东路": "wuweidonglu",
    "古浪路": "gulanglu",
    "祁安路": "qianlu",
    "南大路": "nandalu",
    "丰翔路": "fengxianglu",
    "锦秋路": "jinqiulu",
}

# Stations whose legacy annotation key differs from their canonical CSV name.
ALIAS_KEYS = {
    "上海体育场": "shangtichang",
    "临平路": "linping",
    "龙华中路": "longhuazhong",
}

# Legacy station "宝山" is not present in the CSV station list (the CSV only
# contains 宝山路); keep it selectable so its existing annotation is usable.
EXTRA_STATIONS = {
    "3号线": [{"name": "宝山", "key": "baoshan"}],
}

# ---------------------------------------------------------------------------
# Detection rule templates (mirrors the existing run_*.py scripts)
# ---------------------------------------------------------------------------

_BASE_RULES = [
    {"name": "rule_A", "type": "parallel_line", "ref_line": "line_1",
     "min_arm_torso_angle": 0, "dynamic_angle": True},
    {"name": "rule_B", "type": "parallel_line", "ref_line": "line_2",
     "allow_elbow": True, "dynamic_angle": True},
    {"name": "rule_C", "type": "pass_region", "target_region": "region_1"},
]

_SWITCH_RULE = {"name": "rule_D", "type": "parallel_line", "ref_line": "line_1",
                "anti_parallel": True, "dynamic_angle": True}

_BASE_MAPPING = [
    {"action": "Act1 Call", "rule": "rule_A", "occurrence": 1},
    {"action": "Act2 CloseDoor", "rule": "rule_B", "occurrence": 1},
    {"action": "Act3 CheckGap", "rule": "rule_A", "occurrence": 2},
    {"action": "Act4 CheckLight", "rule": "rule_C", "occurrence": 1},
]

_SWITCH_MAPPING = {"action": "Act5 CheckSwitch", "rule": "rule_D", "occurrence": 1}


def build_config(station_name, has_switch):
    """Build the detection rules + action mapping for a station."""
    rules = list(_BASE_RULES)
    mapping = list(_BASE_MAPPING)
    if has_switch:
        rules.append(dict(_SWITCH_RULE))
        mapping.append(dict(_SWITCH_MAPPING))
    return {
        "station_name": station_name,
        "rules": rules,
        "action_mapping": mapping,
        "n_actions": len(mapping),
    }


# The seven annotated stations (station_key → config).
STATION_CONFIGS = {
    "baoshan": build_config("宝山", has_switch=True),
    "shangtichang": build_config("上体场", has_switch=False),
    "tangqiao": build_config("塘桥", has_switch=False),
    "pudongdadao": build_config("浦东大道", has_switch=True),
    "linping": build_config("临平", has_switch=False),
    "jingansi": build_config("静安寺", has_switch=True),
    "longhuazhong": build_config("龙华中", has_switch=True),
}

# ---------------------------------------------------------------------------
# Default detection parameters (exposed and editable in the web UI)
# ---------------------------------------------------------------------------

DEFAULT_PARAMS = {
    "model_conf": 0.5,             # YOLO keypoint confidence threshold
    "imgsz": 640,                  # model input resolution
    "angle_threshold": 40,         # arm vs reference-line max angle (deg)
    "min_arm_len": 30,             # min arm length in px
    "min_arm_torso_angle": 45,     # arm vs torso min angle (deg)
    "dynamic_angle_coeff": 0.6,    # elbow-bend compensation coefficient
    "hold_frames": 20,             # consecutive hit frames to confirm
    "frame_decay": 2,              # hold counter decay per missed frame
    "cooldown_frames": 90,         # cooldown frames after a fire
    "train_mad_threshold": 20,     # MAD above this → train arriving
    "idle_jump_seconds": 5,        # idle jump-scan interval (seconds)
    "conf_low_threshold": 0.3,     # red keypoints below this
    "conf_mid_threshold": 0.6,     # yellow below this, green above
}

# ---------------------------------------------------------------------------
# Registry helpers
# ---------------------------------------------------------------------------


def _load_csv():
    """Return ``{line: [station_name, ...]}`` from the bundled CSV."""
    stations = {line: [] for line in LINES}
    if not os.path.exists(_CSV_PATH):
        return stations
    with open(_CSV_PATH, encoding="utf-8-sig") as f:
        for row in csv.reader(f):
            if len(row) < 2:
                continue
            line, name = row[0].strip(), row[1].strip()
            if line in stations and name:
                stations[line].append(name)
    # Append legacy extra stations (e.g. 宝山)
    for line, extras in EXTRA_STATIONS.items():
        for extra in extras:
            if extra["name"] not in stations[line]:
                stations[line].append(extra["name"])
    return stations


_STATIONS = _load_csv()


def line_number(line):
    """'3号线' → '3'."""
    return line.replace("号线", "")


def station_key(line, name):
    """Return the lowercase-pinyin key for a station (with alias resolution)."""
    if name in ALIAS_KEYS:
        return ALIAS_KEYS[name]
    for extra in EXTRA_STATIONS.get(line, []):
        if extra["name"] == name:
            return extra["key"]
    return PINYIN.get(name, name)


def get_lines():
    """Return ``[{name, count, annotated}]`` for all lines."""
    result = []
    for line in LINES:
        names = _STATIONS[line]
        annotated = sum(1 for n in names if annotation_file_exists(line, n))
        result.append({"name": line, "count": len(names),
                       "annotated": annotated})
    return result


def get_stations(line):
    """Return station entries for a line: ``[{name, key, annotated, status}]``."""
    entries = []
    for name in _STATIONS.get(line, []):
        key = station_key(line, name)
        annotated = annotation_file_exists(line, name)
        configured = key in STATION_CONFIGS
        status = "annotated" if annotated else "unannotated"
        entries.append({
            "name": name,
            "key": key,
            "annotated": annotated,
            "configured": configured,
            "status": status,
        })
    return entries


def annotation_file_exists(line, name):
    return os.path.exists(resolve_annotation_path(line, name))


def resolve_annotation_path(line, name):
    """Return the path to a station's annotation JSON.

    Prefers the new ``regions_{line}_{key}.json`` naming, then falls back to the
    legacy ``regions_{key}.json`` naming used by the existing seven stations.
    If neither exists, returns the new-format path (for saving new annotations).
    """
    key = station_key(line, name)
    ln = line_number(line)
    new_path = os.path.join(DATA_DIR, f"regions_{ln}_{key}.json")
    legacy_path = os.path.join(DATA_DIR, f"regions_{key}.json")
    if os.path.exists(new_path):
        return new_path
    if os.path.exists(legacy_path):
        return legacy_path
    return new_path


def resolve_background_path(line, name):
    """Return the background image path referenced by a station's annotation."""
    import json
    json_path = resolve_annotation_path(line, name)
    if not os.path.exists(json_path):
        return None
    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)
    bg = data.get("background")
    if not bg or not bg.get("image"):
        return None
    return os.path.join(os.path.dirname(json_path), bg["image"])


def get_config(line, name):
    """Return the detection config (rules/mapping) for a station, or None."""
    return STATION_CONFIGS.get(station_key(line, name))
