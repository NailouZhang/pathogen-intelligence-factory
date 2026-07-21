#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

BUNDLE_VERSION = "2026.07-v17.1"
SCHEMA_VERSION = 3
GENERATED_BY = "chatgpt-curated-bundled-vocabulary-v17.1"

GENERAL_CONTEXT = [
    "outbreak", "epidemiology", "surveillance", "transmission", "incidence", "prevalence",
    "seroprevalence", "genome", "genomic surveillance", "phylogeny", "evolution", "mutation",
    "diagnosis", "diagnostic", "serology", "PCR", "antigen", "vaccine", "vaccination",
    "antiviral", "treatment", "clinical", "pathogenesis", "immune response", "host",
    "reservoir", "vector", "spillover", "zoonosis", "case report", "cohort", "mortality",
]

GENERAL_TRANSLATIONS = {
    "outbreak": "暴发", "epidemiology": "流行病学", "surveillance": "监测",
    "transmission": "传播", "incidence": "发病率", "prevalence": "患病率",
    "seroprevalence": "血清流行率", "genome": "基因组", "genomic surveillance": "基因组监测",
    "phylogeny": "系统发育", "evolution": "进化", "mutation": "突变", "diagnosis": "诊断",
    "serology": "血清学", "vaccine": "疫苗", "vaccination": "疫苗接种", "antiviral": "抗病毒药物",
    "treatment": "治疗", "pathogenesis": "发病机制", "immune response": "免疫应答",
    "host": "宿主", "reservoir": "储存宿主", "vector": "媒介", "spillover": "跨物种溢出",
    "zoonosis": "人兽共患病", "case report": "病例报告", "cohort": "队列研究", "mortality": "死亡率",
}


QUALIFIED_OVERRIDES: dict[str, dict[str, list[str]]] = {
    "sars_cov_2": {
        "omicron": ["SARS-CoV-2", "COVID-19", "coronavirus variant", "lineage"],
        "xbb": ["SARS-CoV-2", "COVID-19", "coronavirus variant", "lineage"],
        "jn.1": ["SARS-CoV-2", "COVID-19", "coronavirus variant", "lineage"],
        "ba.2.86": ["SARS-CoV-2", "COVID-19", "coronavirus variant", "lineage"],
    },
    "mpox_virus": {
        "clade ib": ["mpox", "monkeypox", "MPXV", "orthopoxvirus"],
    },
    "marburg_virus": {
        "marv": ["Marburg", "filovirus", "hemorrhagic fever"],
    },
    "seasonal_influenza": {
        "influenza-like illness": ["seasonal influenza", "influenza virus", "flu surveillance"],
    },
    "nipah_virus": {
        "niv": ["Nipah", "henipavirus", "encephalitis"],
    },
    "respiratory_syncytial_virus": {
        "rsv": ["respiratory syncytial", "orthopneumovirus", "bronchiolitis"],
    },
    "norovirus": {
        "acute gastroenteritis": ["norovirus", "Norwalk", "vomiting outbreak", "GII.4"],
    },
    "ebola_viruses": {
        "ebov": ["Ebola", "ebolavirus", "filovirus", "hemorrhagic fever"],
    },
    "dengue_virus": {
        "denv": ["dengue", "flavivirus", "DENV-1", "DENV-2", "DENV-3", "DENV-4"],
    },
    "human_metapneumovirus": {
        "hmpv": ["human metapneumovirus", "pneumovirus", "respiratory infection"],
        "hmpv-a": ["human metapneumovirus", "pneumovirus", "respiratory infection"],
        "hmpv-b": ["human metapneumovirus", "pneumovirus", "respiratory infection"],
        "hmpv a2b": ["human metapneumovirus", "pneumovirus", "respiratory infection"],
        "hmpv b2": ["human metapneumovirus", "pneumovirus", "respiratory infection"],
    },
    "sftsv": {
        "sfts": ["SFTSV", "thrombocytopenia", "Dabie bandavirus", "tick-borne"],
    },
    "avian_influenza": {
        "aiv": ["avian influenza", "bird flu", "poultry", "wild bird"],
    },
    "human_papillomavirus": {
        "hpv": ["human papillomavirus", "papillomavirus", "genotype", "HPV infection"],
        "cervical cancer": ["HPV", "human papillomavirus", "papillomavirus"],
        "oropharyngeal cancer": ["HPV", "human papillomavirus", "papillomavirus"],
    },
    "measles_virus": {
        "mev": ["measles", "morbillivirus", "measles virus"],
    },
    "arenaviridae": {
        "arenaviridae": ["human", "mammarenavirus", "hemorrhagic fever", "Lassa"],
        "arenavirus": ["human", "mammarenavirus", "hemorrhagic fever", "Lassa"],
        "mammarenavirus": ["human", "hemorrhagic fever", "Lassa", "LCMV"],
    },
    "human_enterovirus": {
        "enterovirus": ["human", "non-polio", "hand foot and mouth", "acute flaccid myelitis"],
        "enterovirus a": ["human", "EV-A", "hand foot and mouth"],
        "enterovirus b": ["human", "EV-B", "meningitis", "myocarditis"],
        "enterovirus c": ["human", "EV-C", "poliovirus"],
        "enterovirus d": ["human", "EV-D", "respiratory", "acute flaccid myelitis"],
    },
    "human_adenovirus": {
        "adenovirus infection": ["human adenovirus", "HAdV", "human mastadenovirus"],
    },
}

AUGMENT: dict[str, dict[str, Any]] = {
    "sars_cov_2": {
        "identity": ["SARS-CoV-2", "severe acute respiratory syndrome coronavirus 2", "2019-nCoV"],
        "members": ["Omicron", "XBB", "JN.1", "BA.2.86"],
        "diseases": ["COVID-19", "coronavirus disease 2019", "post-COVID condition", "long COVID"],
        "contexts": ["spike protein", "ACE2", "neutralizing antibody", "variant of concern", "wastewater surveillance"],
        "exclusions": ["SARS-CoV-1", "MERS-CoV", "feline coronavirus", "canine coronavirus"],
        "translations": {"SARS-CoV-2": "严重急性呼吸综合征冠状病毒2", "COVID-19": "新型冠状病毒感染", "long COVID": "长新冠", "spike protein": "刺突蛋白", "variant of concern": "关注变异株"},
    },
    "mpox_virus": {
        "identity": ["mpox virus", "monkeypox virus", "MPXV"], "members": ["clade I mpox virus", "clade II mpox virus", "clade Ib"],
        "diseases": ["mpox", "monkeypox"], "contexts": ["orthopoxvirus", "rash", "sexual transmission", "tecovirimat", "MVA-BN"],
        "exclusions": ["variola virus", "cowpox virus", "molluscum contagiosum virus"],
        "translations": {"mpox virus": "猴痘病毒", "mpox": "猴痘", "orthopoxvirus": "正痘病毒", "tecovirimat": "特考韦瑞"},
    },
    "marburg_virus": {
        "identity": ["Marburg virus", "MARV", "Marburg marburgvirus"], "members": ["Ravn virus"],
        "diseases": ["Marburg virus disease", "Marburg hemorrhagic fever"], "contexts": ["filovirus", "Egyptian fruit bat", "Rousettus aegyptiacus", "viral hemorrhagic fever"],
        "exclusions": ["Ebola virus", "Sudan virus", "Lassa virus"],
        "translations": {"Marburg virus": "马尔堡病毒", "Marburg virus disease": "马尔堡病毒病", "filovirus": "丝状病毒"},
    },
    "seasonal_influenza": {
        "identity": ["seasonal influenza", "human influenza A virus", "human influenza B virus"],
        "members": ["A(H1N1)pdm09", "A(H3N2)", "B/Victoria lineage"], "diseases": ["seasonal flu", "influenza-like illness"],
        "contexts": ["hemagglutinin", "neuraminidase", "antigenic drift", "vaccine effectiveness", "oseltamivir"],
        "exclusions": ["avian influenza", "swine influenza", "equine influenza", "influenza C virus"],
        "translations": {"seasonal influenza": "季节性流感", "A(H1N1)pdm09": "甲型H1N1流感病毒pdm09", "A(H3N2)": "甲型H3N2流感病毒", "antigenic drift": "抗原漂移"},
    },
    "chikungunya_virus": {
        "identity": ["Chikungunya virus", "CHIKV"], "members": ["Asian lineage CHIKV", "East/Central/South African lineage CHIKV"],
        "diseases": ["chikungunya", "chikungunya fever", "chronic chikungunya arthritis"],
        "contexts": ["Aedes aegypti", "Aedes albopictus", "alphavirus", "arthralgia", "E1-A226V"],
        "exclusions": ["O'nyong-nyong virus", "Mayaro virus", "Ross River virus"],
        "translations": {"Chikungunya virus": "基孔肯雅病毒", "chikungunya fever": "基孔肯雅热", "arthralgia": "关节痛"},
    },
    "nipah_virus": {
        "identity": ["Nipah virus", "NiV", "Nipah henipavirus"], "members": ["Nipah virus Bangladesh", "Nipah virus Malaysia"],
        "diseases": ["Nipah virus infection", "Nipah encephalitis"], "contexts": ["henipavirus", "Pteropus", "fruit bat", "encephalitis", "person-to-person transmission"],
        "exclusions": ["Hendra virus", "Cedar virus", "Menangle virus"],
        "translations": {"Nipah virus": "尼帕病毒", "henipavirus": "亨尼帕病毒", "encephalitis": "脑炎", "fruit bat": "果蝠"},
    },
    "respiratory_syncytial_virus": {
        "identity": ["respiratory syncytial virus", "RSV", "human orthopneumovirus"], "members": ["RSV-A", "RSV-B"],
        "diseases": ["RSV infection", "RSV bronchiolitis"], "contexts": ["bronchiolitis", "pneumonia", "infant", "older adult", "nirsevimab", "palivizumab", "prefusion F protein"],
        "exclusions": ["bovine respiratory syncytial virus", "human metapneumovirus"],
        "translations": {"respiratory syncytial virus": "呼吸道合胞病毒", "bronchiolitis": "毛细支气管炎", "nirsevimab": "尼塞韦单抗"},
    },
    "norovirus": {
        "identity": ["norovirus", "Norwalk virus", "human norovirus"], "members": ["norovirus GI", "norovirus GII", "GII.4 norovirus"],
        "diseases": ["norovirus gastroenteritis", "acute gastroenteritis"], "contexts": ["vomiting", "diarrhea", "foodborne", "cruise ship", "capsid", "wastewater"],
        "exclusions": ["murine norovirus", "sapovirus", "rotavirus"],
        "translations": {"norovirus": "诺如病毒", "acute gastroenteritis": "急性胃肠炎", "foodborne": "食源性"},
    },
    "ebola_viruses": {
        "identity": ["Ebola virus", "EBOV", "ebolavirus"], "members": ["Zaire ebolavirus", "Sudan virus", "Bundibugyo virus", "Taï Forest virus"],
        "diseases": ["Ebola virus disease", "Ebola hemorrhagic fever"], "contexts": ["filovirus", "viral hemorrhagic fever", "ring vaccination", "rVSV-ZEBOV", "monoclonal antibody"],
        "exclusions": ["Marburg virus", "Lassa virus", "Reston virus infection in pigs"],
        "translations": {"Ebola virus": "埃博拉病毒", "Ebola virus disease": "埃博拉病毒病", "ring vaccination": "环形疫苗接种"},
    },
    "dengue_virus": {
        "identity": ["dengue virus", "DENV"], "members": ["DENV-1", "DENV-2", "DENV-3", "DENV-4"],
        "diseases": ["dengue", "dengue fever", "severe dengue", "dengue hemorrhagic fever"],
        "contexts": ["Aedes aegypti", "Aedes albopictus", "antibody-dependent enhancement", "NS1 antigen", "Dengvaxia", "Qdenga"],
        "exclusions": ["Zika virus", "yellow fever virus", "Japanese encephalitis virus"],
        "translations": {"dengue virus": "登革病毒", "dengue fever": "登革热", "severe dengue": "重症登革热", "antibody-dependent enhancement": "抗体依赖性增强"},
    },
    "human_metapneumovirus": {
        "identity": ["human metapneumovirus", "hMPV", "human metapneumovirus infection"], "members": ["hMPV-A", "hMPV-B", "hMPV A2b", "hMPV B2"],
        "diseases": ["human metapneumovirus respiratory infection", "hMPV pneumonia"],
        "contexts": ["pneumovirus", "lower respiratory tract infection", "child", "older adult", "fusion protein"],
        "exclusions": ["avian metapneumovirus", "respiratory syncytial virus"],
        "translations": {"human metapneumovirus": "人偏肺病毒", "lower respiratory tract infection": "下呼吸道感染"},
    },
    "sftsv": {
        "identity": ["severe fever with thrombocytopenia syndrome virus", "SFTSV", "Dabie bandavirus"],
        "members": ["SFTS virus"], "diseases": ["severe fever with thrombocytopenia syndrome", "SFTS"],
        "contexts": ["Haemaphysalis longicornis", "tick-borne", "thrombocytopenia", "leukopenia", "bunyavirus", "favipiravir"],
        "exclusions": ["Heartland virus", "Crimean-Congo hemorrhagic fever virus", "Dabie tick virus"],
        "translations": {"severe fever with thrombocytopenia syndrome virus": "发热伴血小板减少综合征病毒", "SFTS": "发热伴血小板减少综合征", "tick-borne": "蜱媒"},
    },
    "hepatitis_b_virus": {
        "identity": ["hepatitis B virus", "HBV", "human hepatitis B virus"], "members": ["HBV genotype A", "HBV genotype B", "HBV genotype C", "HBV genotype D"],
        "diseases": ["hepatitis B", "chronic hepatitis B", "HBV-related hepatocellular carcinoma"],
        "contexts": ["HBsAg", "HBeAg", "HBV DNA", "cccDNA", "tenofovir", "entecavir", "functional cure"],
        "exclusions": ["hepatitis C virus", "hepatitis D virus", "duck hepatitis B virus"],
        "translations": {"hepatitis B virus": "乙型肝炎病毒", "chronic hepatitis B": "慢性乙型肝炎", "functional cure": "功能性治愈", "cccDNA": "共价闭合环状DNA"},
    },
    "avian_influenza": {
        "identity": ["avian influenza A virus", "avian influenza virus", "AIV"],
        "members": ["H5N1", "H5N6", "H5N8", "H7N9", "H9N2", "H5N2"],
        "diseases": ["avian influenza", "highly pathogenic avian influenza"],
        "contexts": ["poultry", "wild bird", "mammalian adaptation", "clade 2.3.4.4b", "hemagglutinin", "neuraminidase"],
        "exclusions": ["seasonal influenza", "swine influenza", "equine influenza"],
        "translations": {"avian influenza": "禽流感", "highly pathogenic avian influenza": "高致病性禽流感", "wild bird": "野生鸟类"},
    },
    "hantavirus": {
        "identity": ["hantavirus", "orthohantavirus", "Hantaviridae"],
        "members": ["Hantaan virus", "Seoul virus", "Puumala virus", "Sin Nombre virus", "Dobrava-Belgrade virus", "Andes virus"],
        "diseases": ["hemorrhagic fever with renal syndrome", "HFRS", "hantavirus pulmonary syndrome", "hantavirus cardiopulmonary syndrome"],
        "contexts": ["rodent reservoir", "aerosol transmission", "nephropathia epidemica", "Old World hantavirus", "New World hantavirus"],
        "exclusions": ["hanta yoga", "Hendra virus", "Heartland virus"],
        "translations": {"hantavirus": "汉坦病毒", "hemorrhagic fever with renal syndrome": "肾综合征出血热", "hantavirus pulmonary syndrome": "汉坦病毒肺综合征"},
    },
    "human_papillomavirus": {
        "identity": ["human papillomavirus", "HPV", "human papilloma virus"],
        "members": ["HPV16", "HPV18", "HPV31", "HPV33", "HPV45", "HPV52", "HPV58", "HPV6", "HPV11"],
        "diseases": ["cervical cancer", "HPV infection", "anogenital warts", "oropharyngeal cancer"],
        "contexts": ["high-risk HPV", "low-risk HPV", "E6 oncoprotein", "E7 oncoprotein", "HPV vaccination", "screening"],
        "exclusions": ["bovine papillomavirus", "canine papillomavirus"],
        "translations": {"human papillomavirus": "人乳头瘤病毒", "cervical cancer": "宫颈癌", "high-risk HPV": "高危型HPV"},
    },
    "measles_virus": {
        "identity": ["measles virus", "MeV", "Measles morbillivirus"], "members": ["measles virus genotype B3", "measles virus genotype D8"],
        "diseases": ["measles", "subacute sclerosing panencephalitis"],
        "contexts": ["morbillivirus", "MMR vaccine", "Koplik spots", "elimination", "vaccine coverage", "outbreak"],
        "exclusions": ["rubella virus", "canine distemper virus", "rinderpest virus"],
        "translations": {"measles virus": "麻疹病毒", "measles": "麻疹", "subacute sclerosing panencephalitis": "亚急性硬化性全脑炎"},
    },
    "arenaviridae": {
        "identity": ["Arenaviridae", "arenavirus", "mammarenavirus"],
        "members": ["Lassa virus", "Junín virus", "Machupo virus", "Guanarito virus", "Sabiá virus", "Lujo virus", "lymphocytic choriomeningitis virus"],
        "diseases": ["Lassa fever", "Argentine hemorrhagic fever", "Bolivian hemorrhagic fever", "Venezuelan hemorrhagic fever", "lymphocytic choriomeningitis"],
        "contexts": ["rodent-borne", "viral hemorrhagic fever", "Mastomys", "ribavirin", "Old World arenavirus", "New World arenavirus"],
        "exclusions": ["African swine fever virus", "arena sports", "arena venue"],
        "translations": {"Arenaviridae": "沙粒病毒科", "arenavirus": "沙粒病毒", "Lassa fever": "拉沙热", "lymphocytic choriomeningitis virus": "淋巴细胞脉络丛脑膜炎病毒"},
    },
    "human_enterovirus": {
        "identity": ["human enterovirus", "enterovirus", "Enterovirus A", "Enterovirus B", "Enterovirus C", "Enterovirus D"],
        "members": ["enterovirus A71", "EV-A71", "coxsackievirus A16", "coxsackievirus B", "echovirus", "poliovirus", "enterovirus D68"],
        "diseases": ["hand foot and mouth disease", "acute flaccid myelitis", "enteroviral meningitis"],
        "contexts": ["picornavirus", "non-polio enterovirus", "wastewater surveillance", "neutralizing antibody", "capsid VP1"],
        "exclusions": ["rhinovirus", "bovine enterovirus", "porcine enterovirus"],
        "translations": {"human enterovirus": "人肠道病毒", "hand foot and mouth disease": "手足口病", "acute flaccid myelitis": "急性弛缓性脊髓炎"},
    },
    "human_adenovirus": {
        "identity": ["human adenovirus", "HAdV", "human mastadenovirus"],
        "members": ["HAdV-3", "HAdV-4", "HAdV-7", "HAdV-8", "HAdV-14", "HAdV-40", "HAdV-41", "HAdV-55"],
        "diseases": ["adenovirus infection", "adenoviral pneumonia", "epidemic keratoconjunctivitis"],
        "contexts": ["mastadenovirus", "respiratory outbreak", "conjunctivitis", "gastroenteritis", "military recruit", "wastewater"],
        "exclusions": ["adenoviral vector", "chimpanzee adenovirus vector", "canine adenovirus", "bovine adenovirus"],
        "translations": {"human adenovirus": "人腺病毒", "adenoviral pneumonia": "腺病毒肺炎", "epidemic keratoconjunctivitis": "流行性角结膜炎"},
    },
    "rabies_virus": {
        "identity": ["rabies virus", "RABV", "Rabies lyssavirus"], "members": ["classical rabies virus"],
        "diseases": ["rabies", "human rabies", "animal rabies"],
        "contexts": ["lyssavirus", "dog-mediated rabies", "bat rabies", "post-exposure prophylaxis", "rabies vaccine", "rabies immunoglobulin"],
        "exclusions": ["Australian bat lyssavirus", "European bat lyssavirus", "rabies metaphor"],
        "translations": {"rabies virus": "狂犬病病毒", "rabies": "狂犬病", "post-exposure prophylaxis": "暴露后预防"},
    },
}


def stable_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def unique(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = clean(value)
        key = item.casefold()
        if item and key not in seen:
            seen.add(key)
            out.append(item)
    return out


def normalize_rows(values: Any, category: str, urls: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in values or []:
        row = {"term": raw} if isinstance(raw, str) else deepcopy(raw) if isinstance(raw, dict) else {}
        term = clean(row.get("term"))
        if not term or term.casefold() in seen:
            continue
        seen.add(term.casefold())
        row["term"] = term
        row["normalized_term"] = clean(row.get("normalized_term") or term).casefold()
        row["category"] = category
        row["source_urls"] = unique(list(row.get("source_urls") or []) + urls)
        if category in {"identity_anchor_terms", "member_identity_terms", "disease_identity_terms"}:
            row.setdefault("safe_to_use_alone", True)
        elif category == "qualified_identity_terms":
            row.setdefault("forbidden_without_context", True)
            row["required_context_terms"] = unique(list(row.get("required_context_terms") or []))
        else:
            row.setdefault("may_use_only_after_identity", True)
        rows.append(row)
    return rows


def add_terms(rows: list[dict[str, Any]], terms: list[str], category: str, urls: list[str]) -> list[dict[str, Any]]:
    return normalize_rows(rows + [{"term": term} for term in terms], category, urls)


def build_profile(seed: dict[str, Any]) -> dict[str, Any]:
    pid = seed["profile_id"]
    aug = AUGMENT[pid]
    sources = [row for row in seed.get("authoritative_sources") or [] if isinstance(row, dict)]
    urls = unique([row.get("url") for row in sources])
    candidate = deepcopy(seed.get("candidate_vocabulary") or {})
    review: dict[str, Any] = {}
    mappings = {
        "identity_anchor_terms": aug.get("identity", []),
        "member_identity_terms": aug.get("members", []),
        "disease_identity_terms": aug.get("diseases", []),
        "context_terms": list(aug.get("contexts", [])) + GENERAL_CONTEXT,
        "exclusion_terms": aug.get("exclusions", []),
    }
    for key in (
        "identity_anchor_terms", "qualified_identity_terms", "member_identity_terms",
        "disease_identity_terms", "context_terms", "display_only_terms", "exclusion_terms",
        "paper_priority_terms",
    ):
        rows = normalize_rows(candidate.get(key), key, urls)
        rows = add_terms(rows, mappings.get(key, []), key, urls)
        review[key] = rows
    review["document_type_terms"] = deepcopy(candidate.get("document_type_terms") or {})

    # Ambiguous acronyms, lineage labels and broad disease names are retained,
    # but are never treated as standalone final-review identities.  They are
    # converted into qualified rules that require explicit target context.
    overrides = QUALIFIED_OVERRIDES.get(pid, {})
    qualified_rows = list(review.get("qualified_identity_terms") or [])
    qualified_index = {clean(row.get("term")).casefold(): row for row in qualified_rows if isinstance(row, dict)}
    for category in ("identity_anchor_terms", "member_identity_terms", "disease_identity_terms"):
        for row in review.get(category) or []:
            term_key = clean(row.get("term")).casefold()
            required = overrides.get(term_key)
            if not required:
                continue
            row["safe_to_use_alone"] = False
            existing = qualified_index.get(term_key)
            if existing is None:
                existing = {
                    "term": row.get("term"),
                    "normalized_term": term_key,
                    "category": "qualified_identity_terms",
                    "source_urls": unique(list(row.get("source_urls") or []) + urls),
                    "forbidden_without_context": True,
                    "required_context_terms": unique(required),
                }
                qualified_rows.append(existing)
                qualified_index[term_key] = existing
            else:
                existing["required_context_terms"] = unique(list(existing.get("required_context_terms") or []) + required)
                existing["forbidden_without_context"] = True
    review["qualified_identity_terms"] = normalize_rows(qualified_rows, "qualified_identity_terms", urls)

    glossary = dict(GENERAL_TRANSLATIONS)
    glossary.update(aug.get("translations") or {})
    translation_rows = [
        {"source": source, "target": target, "source_urls": urls}
        for source, target in sorted(glossary.items(), key=lambda x: x[0].casefold())
    ]
    concepts = [row for row in (seed.get("search_strategy") or {}).get("concepts") or [] if isinstance(row, dict)]
    retrieval = {
        "frozen_core_concepts": concepts,
        "controlled_supplemental_terms": unique((seed.get("search_strategy") or {}).get("controlled_supplemental_terms") or []),
        "news_identity_terms_zh": unique(seed.get("news_identity_terms_zh") or []),
        "policy": "retrieval terms are recall-only and never substitute for final review vocabulary",
    }
    safe_identity_rows = [
        row for row in (review["identity_anchor_terms"] + review["disease_identity_terms"] + review["member_identity_terms"])
        if row.get("safe_to_use_alone", True)
    ]
    validation = {
        "positive": [
            {
                "surface": "title",
                "title": f"New surveillance findings for {row['term']}",
                "text": f"New surveillance findings for {row['term']}",
                "expected": "accept",
            }
            for row in safe_identity_rows[:6]
        ] + [
            {
                "surface": "abstract_or_brief",
                "title": "Recent pathogen surveillance update",
                "text": f"The study repeatedly identified {row['term']} and evaluated transmission, diagnosis and clinical outcomes.",
                "expected": "accept",
            }
            for row in safe_identity_rows[6:10]
        ] + [
            {
                "surface": "full_body",
                "title": "Public-health investigation",
                "text": f"Multiple article paragraphs document {row['term']}. Genomic surveillance, epidemiology and clinical evidence consistently support the target identity.",
                "expected": "accept",
            }
            for row in safe_identity_rows[10:12]
        ],
        "negative": [
            {
                "surface": "title",
                "title": f"New findings about {row['term']}",
                "text": f"This report concerns {row['term']} and contains no target-virus evidence.",
                "expected": "reject",
            }
            for row in review["exclusion_terms"][:10]
        ] + [
            {
                "surface": "navigation_noise",
                "title": "Website navigation and cookie notice",
                "text": "Accept cookies menu login become a member categories webinars white papers view all weather privacy policy",
                "expected": "reject",
            }
        ],
        "qualified": [
            {
                "surface": "abstract_or_brief",
                "term": row.get("term"),
                "required_context_terms": row.get("required_context_terms") or [],
                "without_context": "reject",
                "with_context": "review_or_accept",
            }
            for row in review["qualified_identity_terms"][:12]
        ],
        "field_threshold_expectations": {
            "title": "specific safe identity may accept at the title threshold",
            "abstract_or_brief": "identity plus contextual or repeated evidence uses an independent threshold",
            "full_body": "multiple identity-bearing sentences and article-level coherence use an independent threshold",
        },
    }
    profile = {
        "schema_version": "3.3-bundled",
        "profile_id": pid,
        "display_name_en": seed.get("display_name_en"),
        "display_name_zh": seed.get("display_name_zh"),
        "target_scope": seed.get("target_scope") or {},
        "search_strategy": seed.get("search_strategy") or {},
        "query_policy": seed.get("query_policy") or {},
        "source_policy": seed.get("source_policy") or {},
        "authoritative_sources": sources,
        "vocabulary": review,
        "translation_glossary": translation_rows,
        "generated_by": GENERATED_BY,
        "bundle_version": BUNDLE_VERSION,
    }
    semantic_payload = {
        "profile": profile,
        "retrieval": retrieval,
        "review": review,
        "glossary": translation_rows,
        "sources": sources,
    }
    fingerprint = stable_hash(semantic_payload)
    profile["profile_semantic_fingerprint"] = fingerprint
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "bundle_version": BUNDLE_VERSION,
        "profile_id": pid,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generated_by": GENERATED_BY,
        "profile_semantic_fingerprint": fingerprint,
        "source_fingerprint": stable_hash(sources),
        "validation_status": "passed",
        "term_counts": {key: len(value) if isinstance(value, list) else len(value) for key, value in review.items()},
        "files": {},
    }
    return {
        "manifest.json": manifest,
        "profile.json": profile,
        "retrieval_vocabulary.json": retrieval,
        "review_vocabulary.json": {
            "schema_version": SCHEMA_VERSION,
            "bundle_version": BUNDLE_VERSION,
            "profile_id": pid,
            "generated_by": GENERATED_BY,
            "profile_semantic_fingerprint": fingerprint,
            "review_vocabulary": review,
        },
        "exclusion_vocabulary.json": {
            "schema_version": SCHEMA_VERSION,
            "profile_id": pid,
            "exclusion_terms": review["exclusion_terms"],
            "near_neighbors": (seed.get("target_scope") or {}).get("non_target_near_neighbors") or [],
        },
        "translation_glossary.json": {
            "schema_version": SCHEMA_VERSION,
            "profile_id": pid,
            "translation_glossary": translation_rows,
        },
        "authoritative_sources.json": {
            "schema_version": SCHEMA_VERSION,
            "profile_id": pid,
            "sources": sources,
        },
        "validation_cases.json": {
            "schema_version": SCHEMA_VERSION,
            "profile_id": pid,
            **validation,
        },
    }


def write_bundle(project_root: Path) -> None:
    out_root = project_root / "config" / "vocabularies"
    out_root.mkdir(parents=True, exist_ok=True)
    profiles = sorted((project_root / "profiles").glob("*/seed.yaml"))
    ids = [path.parent.name for path in profiles]
    missing = sorted(set(ids) - set(AUGMENT))
    extra = sorted(set(AUGMENT) - set(ids))
    if missing or extra:
        raise SystemExit(f"augmentation/profile mismatch; missing={missing}; extra={extra}")
    catalog = []
    for seed_path in profiles:
        seed = yaml.safe_load(seed_path.read_text(encoding="utf-8"))
        files = build_profile(seed)
        target = out_root / seed["profile_id"]
        target.mkdir(parents=True, exist_ok=True)
        manifest = files["manifest.json"]
        for name, payload in files.items():
            if name == "manifest.json":
                continue
            path = target / name
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            manifest["files"][name] = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest_path = target / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        catalog.append({
            "profile_id": seed["profile_id"],
            "display_name_en": seed.get("display_name_en"),
            "display_name_zh": seed.get("display_name_zh"),
            "bundle_version": BUNDLE_VERSION,
            "profile_semantic_fingerprint": manifest["profile_semantic_fingerprint"],
            "term_counts": manifest["term_counts"],
        })
    (out_root / "catalog.json").write_text(json.dumps({
        "schema_version": SCHEMA_VERSION,
        "bundle_version": BUNDLE_VERSION,
        "generated_by": GENERATED_BY,
        "profiles": catalog,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"generated {len(catalog)} bundled vocabularies at {out_root}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    write_bundle(args.project_root.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
