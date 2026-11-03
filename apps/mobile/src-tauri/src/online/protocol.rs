//! The current shared schemas are compiled into the binary; no remote retriever.
use super::error::{Error, Result};
use serde::{
    de::{self, MapAccess, SeqAccess, Visitor},
    Deserialize, Deserializer,
};
use serde_json::{json, Value};
use std::{
    collections::{BTreeMap, BTreeSet},
    sync::OnceLock,
};

const BATTLE: &str =
    include_str!("../../../../../packages/battle-protocol/src/ptcg_protocol/schema/v2.schema.json");
const SESSION: &str = include_str!(
    "../../../../../packages/online-client/src/ptcg_online_client/schemas/session.schema.json"
);
const LOBBY: &str = include_str!(
    "../../../../../packages/online-client/src/ptcg_online_client/schemas/lobby.schema.json"
);
const MATCH: &str = include_str!(
    "../../../../../packages/online-client/src/ptcg_online_client/schemas/match.schema.json"
);
static SCHEMAS: OnceLock<BTreeMap<String, jsonschema::Validator>> = OnceLock::new();
pub const MAX_RESPONSE: usize = 2 * 1024 * 1024;

// serde_json::Value alone accepts duplicate keys. Reject ambiguity before schema validation.
struct Unique(Value);
impl<'de> Deserialize<'de> for Unique {
    fn deserialize<D: Deserializer<'de>>(d: D) -> std::result::Result<Self, D::Error> {
        struct V;
        impl<'de> Visitor<'de> for V {
            type Value = Unique;
            fn expecting(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
                f.write_str("unambiguous JSON")
            }
            fn visit_bool<E: de::Error>(self, v: bool) -> std::result::Result<Unique, E> {
                Ok(Unique(v.into()))
            }
            fn visit_i64<E: de::Error>(self, v: i64) -> std::result::Result<Unique, E> {
                Ok(Unique(v.into()))
            }
            fn visit_u64<E: de::Error>(self, v: u64) -> std::result::Result<Unique, E> {
                Ok(Unique(v.into()))
            }
            fn visit_f64<E: de::Error>(self, _: f64) -> std::result::Result<Unique, E> {
                Err(E::custom("non-integer"))
            }
            fn visit_str<E: de::Error>(self, v: &str) -> std::result::Result<Unique, E> {
                Ok(Unique(v.into()))
            }
            fn visit_string<E: de::Error>(self, v: String) -> std::result::Result<Unique, E> {
                Ok(Unique(v.into()))
            }
            fn visit_unit<E: de::Error>(self) -> std::result::Result<Unique, E> {
                Ok(Unique(Value::Null))
            }
            fn visit_seq<A: SeqAccess<'de>>(
                self,
                mut a: A,
            ) -> std::result::Result<Unique, A::Error> {
                let mut rows = Vec::new();
                while let Some(v) = a.next_element::<Unique>()? {
                    rows.push(v.0);
                    if rows.len() > 100000 {
                        return Err(de::Error::custom("limit"));
                    }
                }
                Ok(Unique(rows.into()))
            }
            fn visit_map<A: MapAccess<'de>>(
                self,
                mut a: A,
            ) -> std::result::Result<Unique, A::Error> {
                let mut out = serde_json::Map::new();
                while let Some(key) = a.next_key::<String>()? {
                    if out.contains_key(&key) {
                        return Err(de::Error::custom("duplicate"));
                    }
                    out.insert(key, a.next_value::<Unique>()?.0);
                    if out.len() > 100000 {
                        return Err(de::Error::custom("limit"));
                    }
                }
                Ok(Unique(out.into()))
            }
        }
        d.deserialize_any(V)
    }
}
pub fn unpack(raw: &[u8]) -> Result<Value> {
    if raw.len() > MAX_RESPONSE {
        return Err(Error::new("RESPONSE_TOO_LARGE"));
    }
    let value = serde_json::from_slice::<Unique>(raw)
        .map_err(|_| Error::invalid())?
        .0;
    bounded(&value)?;
    Ok(value)
}
fn bounded(value: &Value) -> Result<()> {
    let mut pending = vec![(value, 0)];
    let mut count = 0;
    while let Some((v, depth)) = pending.pop() {
        count += 1;
        if count > 100000 || depth > 32 {
            return Err(Error::invalid());
        }
        match v {
            Value::Array(rows) => pending.extend(rows.iter().map(|v| (v, depth + 1))),
            Value::Object(rows) => pending.extend(rows.values().map(|v| (v, depth + 1))),
            Value::Number(n) if n.is_f64() => return Err(Error::invalid()),
            _ => (),
        }
    }
    Ok(())
}
fn schemas() -> &'static BTreeMap<String, jsonschema::Validator> {
    SCHEMAS.get_or_init(|| {
        let mut out = BTreeMap::new();
        for source in [BATTLE, SESSION, LOBBY, MATCH] {
            let root: Value = serde_json::from_str(source).expect("packaged schema JSON");
            for kind in root["$defs"].as_object().unwrap().keys() {
                if out.contains_key(kind) {
                    continue;
                }
                let schema = json!({"$defs":root["$defs"],"$ref":format!("#/$defs/{kind}")});
                let validator = jsonschema::draft202012::options()
                    .should_validate_formats(true)
                    .build(&schema)
                    .expect("packaged schema references");
                out.insert(kind.clone(), validator);
            }
        }
        out
    })
}
pub fn checked(kind: &str, value: Value) -> Result<Value> {
    bounded(&value)?;
    let limit = match kind {
        "Command" | "DeckSubmission" | "Capabilities" => 16384,
        "MatchSnapshot" => 524288,
        "ProtocolError" => 4096,
        _ => MAX_RESPONSE,
    };
    if serde_json::to_vec(&value)?.len() > limit
        || !schemas().get(kind).is_some_and(|v| v.is_valid(&value))
    {
        return Err(Error::invalid());
    }
    semantics(kind, &value)?;
    Ok(value)
}
pub fn segment(value: &str, command: bool) -> Result<&str> {
    let valid = if command {
        !value.is_empty()
            && value.len() <= 128
            && value
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
    } else {
        value.len() == 32
            && value
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    };
    if !valid {
        return Err(Error::new("INVALID_ID"));
    }
    Ok(value)
}
fn unique(rows: &Value, field: &str) -> bool {
    let rows = rows.as_array().unwrap();
    rows.iter()
        .map(|r| r[field].to_string())
        .collect::<BTreeSet<_>>()
        .len()
        == rows.len()
}
fn frame(f: &Value) -> Result<()> {
    let own = &f["observation"]["self"];
    if own["handCount"].as_u64() != Some(own["hand"].as_array().unwrap().len() as u64)
        || f["done"] != (f["status"] != "playing")
        || (!f["winner"].is_null() && f["status"] != "finished")
    {
        return Err(Error::invalid());
    }
    let mut refs = BTreeSet::new();
    for side in ["self", "opponent"] {
        let side = &f["observation"][side];
        if side["bench"].as_array().unwrap().len() as u64 > side["benchCapacity"].as_u64().unwrap()
            || !unique(&side["faceUpPrizes"], "index")
        {
            return Err(Error::invalid());
        }
        if side["faceUpPrizes"]
            .as_array()
            .unwrap()
            .iter()
            .any(|p| p["index"].as_u64().unwrap() >= side["prizeCount"].as_u64().unwrap())
        {
            return Err(Error::invalid());
        }
        for zone in ["active", "bench"] {
            for row in side[zone].as_array().unwrap() {
                if !refs.insert(row["ref"].to_string()) {
                    return Err(Error::invalid());
                }
            }
        }
    }
    for row in f["observation"]["stadium"].as_array().unwrap() {
        if !refs.insert(row["ref"].to_string()) {
            return Err(Error::invalid());
        }
    }
    let mut previous = None;
    for event in f["events"].as_array().unwrap() {
        let n = event["seq"].as_u64().unwrap();
        if previous.is_some_and(|old| n <= old) {
            return Err(Error::invalid());
        }
        previous = Some(n);
    }
    Ok(())
}
fn semantics(kind: &str, v: &Value) -> Result<()> {
    let mut valid = true;
    match kind {
        "DeckSubmission" => {
            valid = unique(&v["entries"], "printingId")
                && v["entries"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|r| r["quantity"].as_u64().unwrap())
                    .sum::<u64>()
                    == 60
        }
        "Capabilities" => {
            valid =
                v["acceptingMatches"] != true || !v["ruleReleaseIds"].as_array().unwrap().is_empty()
        }
        "MatchSnapshot" => {
            frame(v)?;
            let d = &v["decision"];
            if !d.is_null() {
                valid = v["done"] == false && d["actor"] == v["viewerSeat"];
                if d["kind"] == "options" {
                    valid &= unique(&d["options"], "id");
                } else {
                    valid &= unique(&d["candidates"], "ref")
                        && d["min"].as_u64().unwrap() <= d["max"].as_u64().unwrap()
                        && d["max"].as_u64().unwrap()
                            <= d["candidates"].as_array().unwrap().len() as u64;
                    if d["hidden"] == true {
                        valid &= d.get("deckInspection").is_none();
                        for c in d["candidates"].as_array().unwrap() {
                            if c.as_object().unwrap().len() == 1 && c.get("ref").is_some() {
                                continue;
                            }
                            if c.as_object()
                                .unwrap()
                                .keys()
                                .map(String::as_str)
                                .collect::<BTreeSet<_>>()
                                != BTreeSet::from([
                                    "ref",
                                    "card",
                                    "revealedPrizeIndex",
                                    "revealedPrizeSide",
                                ])
                            {
                                return Err(Error::invalid());
                            }
                            let side = c["revealedPrizeSide"].as_str().unwrap();
                            let visible = v["observation"][side]["faceUpPrizes"]
                                .as_array()
                                .unwrap()
                                .iter()
                                .find(|p| p["index"] == c["revealedPrizeIndex"])
                                .map(|p| &p["card"]);
                            if let Some(p) = visible {
                                let mut presentation =
                                    p.get("presentation").cloned().unwrap_or(json!({}));
                                presentation.as_object_mut().unwrap().remove("prizeIndex");
                                valid &= p["cardId"] == c["card"]["cardId"]
                                    && p["name"] == c["card"]["name"]
                                    && presentation
                                        == c["card"]
                                            .get("presentation")
                                            .cloned()
                                            .unwrap_or(json!({}));
                            } else {
                                valid = false;
                            }
                        }
                    }
                    if let Some(i) = d.get("deckInspection") {
                        valid &= i["cards"]
                            .as_array()
                            .unwrap()
                            .iter()
                            .map(|r| r["count"].as_u64().unwrap())
                            .sum::<u64>()
                            == i["total"].as_u64().unwrap();
                    }
                }
            }
        }
        "ReplayPage" => {
            let mut previous: Option<&Value> = None;
            for f in v["frames"].as_array().unwrap() {
                frame(f)?;
                if let Some(p) = previous {
                    valid &= f["frameSeq"].as_u64() > p["frameSeq"].as_u64()
                        && f["matchSeq"].as_u64() >= p["matchSeq"].as_u64()
                        && f["stateVersion"].as_u64() >= p["stateVersion"].as_u64();
                }
                previous = Some(f);
            }
        }
        _ => (),
    }
    if valid {
        Ok(())
    } else {
        Err(Error::invalid())
    }
}
