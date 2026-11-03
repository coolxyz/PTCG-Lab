"""Prepare explicit, licensed display metadata for iOS; no engine, Python or user state."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import re
import sys

ROOT=Path(__file__).resolve().parents[2]
if __package__ in (None, ""):
    sys.path.insert(0,str(ROOT))
from scripts.packaging.common import new_output, record
from scripts.public.policy import safe_file
sys.path.insert(0,str(ROOT/"packages/engine-provider/src"))
from ptcg_engine_provider.catalog import MemoryCatalog

FIELDS=("printingId","cnName","englishName","engineId","category","subtype","stage","hp",
        "pokemonType","mark","rarity","productCode","collectorNumber","productName",
        "nameLimitKey","isBasicPokemon","basicEnergyType","aceSpec","effectStatus","attacks","aliases")


def build(assets, output, *, root=ROOT):
    assets, root=Path(assets),Path(root)
    if Path(output).resolve().is_relative_to(assets.resolve()) or assets.resolve().is_relative_to(Path(output).resolve()):
        raise ValueError("Resource source and output must be separate directories")
    raw=safe_file(assets,"data/catalog/catalog.json").read_bytes()
    catalog=json.loads(raw)
    details=json.loads(safe_file(assets,"data/catalog/card-details.json").read_bytes())["cards"]
    primary=json.loads(safe_file(root,"resources/artwork/builtin-index.json").read_bytes())
    faces=json.loads(safe_file(root,"resources/artwork/builtin-snapshot.json").read_bytes())
    expected=sha256(raw).hexdigest()
    if any(index["catalogFileSha256"]!=expected for index in (primary,faces)):
        raise ValueError("Display indexes do not match the explicit approved catalogue")
    if any(primary[k]!=faces[k] for k in ("repository","commit")):
        raise ValueError("Display index source identities differ")
    cards=[];images={};known={c["printingId"]:c for c in catalog["cards"]}
    for row in primary["cards"]:
        if row["printingId"] not in known or known[row["printingId"]]["engineId"]!=row["engineId"]:
            raise ValueError("Printing-to-artwork identity differs")
        images[row["printingId"]]={k:row[k] for k in ("path","blob")}
    for card in catalog["cards"]:
        pid=card["printingId"]
        if pid not in images:
            for locale in ("zh-Hans","zh-Hant"):
                for image in card.get("images",[]):
                    match=re.fullmatch(r"/api/sync/images/([a-f0-9]{40})/([0-9]+)",image.get("url") or "")
                    if image.get("locale")==locale and image.get("equivalenceReviewed") is True and match and match[1]==faces["commit"] and match[2] in faces["images"]:
                        images[pid]=faces["images"][match[2]];break
                if pid in images:break
        info=details.get(pid,{})
        cards.append({**{k:card.get(k) for k in FIELDS},"hasArtwork":pid in images,
                      "chineseDetails":{k:info[k] for k in ("sections","textStatus","revision") if k in info}})
    output=new_output(output)
    def write(name,value):
        content=(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":"))+"\n").encode()
        (output/name).write_bytes(content)
        return sha256(content).hexdigest()
    files={
        "catalog.json":write("catalog.json",{"version":catalog["version"],"displayCatalogVersion":MemoryCatalog.from_catalog(catalog).catalog_version,"cards":cards}),
        "artwork.json":write("artwork.json",{"repository":primary["repository"],"commit":primary["commit"],"cards":images}),
    }
    record(output/"manifest.json",{"schema":1,"catalogVersion":catalog["version"],"files":files})
    return {"cards":len(cards),"indexedArtwork":len(images),"catalogVersion":catalog["version"],
            "includesRules":False,"includesUserData":False,"imageDownloads":0}


def bundle(resources,output):
    """A single display document for native staged installation, never an artwork ZIP."""
    resources=Path(resources)
    content={name:safe_file(resources,name).read_bytes() for name in ("catalog.json","artwork.json","manifest.json")}
    manifest=json.loads(content['manifest.json'])
    if manifest.get('schema')!=1 or set(manifest.get('files',{}))!={'catalog.json','artwork.json'}:
        raise ValueError('Invalid mobile resource manifest')
    if any(sha256(content[name]).hexdigest()!=digest for name,digest in manifest['files'].items()):
        raise ValueError('Mobile resource hash mismatch')
    catalog=json.loads(content['catalog.json']);artwork=json.loads(content['artwork.json'])
    if catalog.get('version')!=manifest['catalogVersion'] or not re.fullmatch('[a-f0-9]{64}',catalog.get('displayCatalogVersion','')):
        raise ValueError('Mobile display identity missing or mismatched')
    ids={c['printingId'] for c in catalog['cards']}
    if not set(artwork['cards']).issubset(ids):raise ValueError('Unknown artwork printing identity')
    data=(json.dumps({'schema':1,'kind':'ptcg-mobile-display',**{name.removesuffix('.json'):raw.decode('utf8') for name,raw in content.items()}},ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n').encode()
    if len(data)>80*1024*1024:raise ValueError('Mobile display bundle exceeds 80 MiB')
    output=new_output(output);name='mobile-display.json';(output/name).write_bytes(data)
    digest=sha256(data).hexdigest();(output/(name+'.sha256')).write_text(digest+'  '+name+'\n')
    report={'schema':1,'file':name,'sha256':digest,'bytes':len(data),'cards':len(ids),'catalogVersion':catalog['version'],'displayCatalogVersion':catalog['displayCatalogVersion'],'includesRules':False,'includesUserData':False,'includesImages':False}
    record(output/'DISPLAY-RELEASE.json',report)
    return report


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets",required=True,type=Path)
    parser.add_argument("--output",required=True,type=Path)
    args=parser.parse_args()
    print(json.dumps(build(args.assets,args.output)))
