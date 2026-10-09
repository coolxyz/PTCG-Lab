"""Lossless formatting normalization for matching reviewed Chinese rule text.

No words, conditions, numbers, or sentences are dropped. Unknown wiki markup
remains unmatched rather than being stripped and mistaken for an empty effect.
"""
import html
import re
import unicodedata


def text(value):
    value = unicodedata.normalize('NFKC', html.unescape(str(value or '')))
    value = value.replace('\u200c','').replace('\u200b','')
    value = value.replace('{{V}}', 'V')
    value = re.sub(r'(?<=[次量数])x(?=\d)', '×', value)
    value = value.replace(',', '，')
    value = value.replace('加入手牌，并重洗牌库。', '加入手牌。并重洗牌库。')
    value = re.sub(r'<br\s*/?>', '', value, flags=re.I)
    value = re.sub(r"'''(.*?)'''", r'「\1」', value)
    value = re.sub(r"''(.*?)''", r'\1', value)
    value = value.replace('{{ex}}', '「ex」')
    def template(m):
        parts = m[1].split('|')
        if parts[0] in ('e', 'TCG', 'TCGPM', '拥有规则的宝可梦', '寶可夢ex', '宝可梦ex') and len(parts) > 1:
            return parts[-1]
        return m[0]
    for _ in range(5):
        revised = re.sub(r'\{\{([^{}]+)\}\}', template, value)
        if revised == value:
            break
        value = revised
    value = re.sub(r'\[\[([^\[\]]+)\]\]', lambda m: m[1].split('|')[-1], value)
    # These delimiters denote the same printed emphasis/energy/status labels.
    value = value.translate(str.maketrans({'【':'「','】':'」','“':'「','”':'」','［':'[','］':']'}))
    # Wiki energy icons have no surrounding punctuation, unlike dataset icons.
    value = re.sub(r'「([草火水雷超斗恶钢妖龙无]|无色)」', r'\1', value)
    value = value.replace('无色','无')
    value = re.sub(r'「(基础|1阶进化|2阶进化|撤退|昏厥|睡眠|中毒|麻痹|混乱|灼伤)」', r'\1', value)
    value = value.replace('「ex」', 'ex').replace('「V」', 'V')
    return re.sub(r'\s+', '', value)
