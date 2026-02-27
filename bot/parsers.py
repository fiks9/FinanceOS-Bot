import re

def parse_natural_amount(text: str) -> float | None:
    """Парсить суму з вільного тексту (розуміє '25 тисяч', '25к', 'півтори' і т.д.)."""
    text = text.lower().strip()
    text = text.replace(",", ".")
    
    # 1. Пошук текстових "півтори", "пів" окремо (вони перетворюватимуться на цифру для Regex)
    text = text.replace("півтори", "1.5").replace("півтора", "1.5").replace("пів", "0.5")

    # Словник текстових чисел
    word_to_num = {
        "одинадцять": 11, "дванадцять": 12, "тринадцять": 13, 
        "чотирнадцять": 14, "п'ятнадцять": 15, "пятнадцять": 15,
        "шістнадцять": 16, "сімнадцять": 17, "вісімнадцять": 18, 
        "дев'ятнадцять": 19, "девятнадцять": 19,
        "десять": 10, "двадцять": 20, "тридцять": 30, "сорок": 40, "п'ятдесят": 50, 
        "пятдесят": 50, "шістдесят": 60, "сімдесят": 70, "вісімдесят": 80, 
        "дев'яносто": 90, "девяносто": 90,
        "сто": 100, "двісті": 200, "триста": 300, "чотириста": 400,
        "п'ятсот": 500, "пятсот": 500, "шістсот": 600, "сімсот": 700,
        "вісімсот": 800, "дев'ятсот": 900, "девятсот": 900,
        "один": 1, "одна": 1, "два": 2, "дві": 2, "три": 3, "чотири": 4, 
        "п'ять": 5, "пять": 5, "шість": 6, "сім": 7, "вісім": 8, 
        "дев'ять": 9, "девять": 9
    }

    # 1. Пошук множників
    multiplier = 1
    if re.search(r'\b(мільйон|мільйона|мільйонів|млн)\b', text):
        multiplier = 1000000
    elif re.search(r'\b(тисяч|тисячі|тисячу|тисяча|тис)\b', text) or re.search(r'\d\s*[кk]\b', text) or re.search(r'\d[кk]', text.replace(' ', '')):
        multiplier = 1000
    
    # 2. Спочатку шукаємо число цифрами
    clean_text = text.replace(' ', '')
    for m in ["мільйон", "мільйона", "мільйонів", "млн", "тисячі", "тисячу", "тисяча", "тисяч", "тис", "к", "k", "k"]:
        clean_text = clean_text.replace(m, "")
    
    num_match = re.search(r'(\d+(?:\.\d+)?)', clean_text)
    if num_match:
        try:
            val = float(num_match.group(1)) * multiplier
            if 0 < val < 10000000:
                return val
        except ValueError:
            pass
            
    # 3. Якщо цифр немає, шукаємо словесні числа
    total = 0
    current_val = 0
    words = re.findall(r'[а-яіїєґ\']+', text)
    
    for w in words:
        if w in word_to_num:
            current_val += word_to_num[w]
        elif w in ["тисяч", "тисячі", "тис", "тисячу"]:
            total += (current_val if current_val > 0 else 1) * 1000
            current_val = 0
        elif w in ["мільйон", "млн", "мільйона", "мільйонів"]:
            total += (current_val if current_val > 0 else 1) * 1000000
            current_val = 0

    total += current_val
    
    if total > 0:
        if total < 1000 and multiplier > 1 and "тисяч" not in text and "млн" not in text:
            total *= multiplier
        
        if 0 < total < 10000000:
            return float(total)
            
    return None
