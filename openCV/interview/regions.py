"""Region-matched alternatives for the identity test.

The true detail must not stand out by its looks. Two examples of how it can:
  * a person with an English name from a Canadian city shown only Indian names and Indian cities:
    their own name and city are the only "local" words, an oddball, which is slow for its own sake.
  * a big city among small towns: the only big city stands out.
So the alternatives must:
  * names: share the true name's style (South Asian, English/European, ...) and gender; mothers'
    names come from the same parent generation.
  * cities: lie in the same province/state (else the same country), have a similar size (tier), and
    lie more than 60 km away, so they're not the person's own suburbs.
Anything we can't place is asked about once in the console, or the operator types the alternatives.
Coordinates are approximate (about 5 km); they're only used for the 60 km rule and "nearby first".
"""
from __future__ import annotations

import math
import random
import unicodedata
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------- names
# Team members' names are left out on purpose: in a teammate's test they'd be familiar words.
STYLES = {
    "south_asian": "South Asian",
    "anglo": "English / European",
    "east_asian": "East / Southeast Asian",
    "middle_eastern": "Middle Eastern / North African",
    "hispanic": "Hispanic / Latin American",
    "african": "African",
}
_NAMES = {
    "south_asian": {
        "m": "Aarav Rohan Kabir Ishaan Vikram Arjun Nikhil Rahul Karan Siddharth Aditya Varun Yash Dev Abhinav "
             "Harsh Manav Tanish Pranav Kunal Akash Amit Gaurav Rajat Sahil Vivek Ankit Mohit Ayush Arnav",
        "f": "Priya Ananya Meera Kavya Sneha Riya Isha Divya Tanvi Nisha Aditi Shreya Simran Nandini Aishwarya "
             "Pallavi Kriti Sakshi Muskan Jasleen Ishita Diya Komal Mansi Tanya",
        "mother": "Sunita Anita Meena Kavita Rekha Neelam Pooja Asha Lakshmi Geeta Seema Ritu Poonam Shalini Usha "
                  "Nirmala Renu Sarita Manju Anjali Suman Vandana Alka Madhu Rashmi Harpreet Jaspreet Manjit",
        "last": "Sharma Verma Gupta Singh Kumar Patel Shah Mehta Joshi Agarwal Malhotra Kapoor Chopra Reddy Rao "
                "Nair Iyer Menon Bansal Arora Khanna Bhatia Sethi Grewal Sandhu Gill Dhillon Sidhu Desai Mishra "
                "Pandey Tiwari Chauhan Saxena",
    },
    "anglo": {
        "m": "James Daniel Ryan Lucas Ethan Noah Liam Owen Jacob Nathan Adam Matthew Tyler Connor Logan Dylan "
             "Evan Cole Brandon Justin Kyle Jordan Austin Mason Carter Hunter Benjamin Thomas Jack Luke",
        "f": "Sophie Hannah Chloe Olivia Emma Grace Emily Claire Laura Anna Sarah Jessica Megan Rachel Kayla "
             "Taylor Brooke Madison Abigail Ava Lauren Paige Morgan Amber Jenna",
        "mother": "Linda Susan Karen Helen Julie Lisa Michelle Jennifer Kimberly Tracy Kelly Angela Shannon "
                  "Heather Wendy Brenda Donna Sandra Debbie Cheryl Lori Dawn Tammy Christine Colleen Shelley "
                  "Patricia Barbara Nancy Diane",
        "last": "Smith Johnson Brown Wilson Taylor Anderson Thompson Campbell Stewart Mitchell Robinson Clarke "
                "Walker Wright Scott Murray Morrison Fraser Graham Hughes Bennett Cooper Parker Reid Hamilton "
                "Ross Young King Bell Ward Miller Davis Martin White Harris",
    },
    "east_asian": {
        "m": "Wei Jun Hao Ming Tao Lei Yang Kenji Hiro Takumi Haruto Ren Minh Duc Tuan Jin Hyun Sung Kai Feng",
        "f": "Mei Yuki Ling Xin Yan Hana Sakura Aiko Mai Linh Thu Jiwoo Min Yuna Jia Hui Lan Na Xia Ying",
        "mother": "Li Hong Ying Hua Fang Keiko Yoko Kumiko Hoa Miran Kyung Jing Ping Xiu Qing Yumi Huong Lien",
        "last": "Wang Li Zhang Liu Chen Yang Huang Zhao Wu Zhou Lin Wong Chan Lee Ng Kim Park Choi Nguyen Tran "
                "Pham Le Tanaka Sato Suzuki Takahashi Kobayashi Ho Lam Leung",
    },
    "middle_eastern": {
        "m": "Omar Yusuf Ahmed Ali Hassan Khalid Karim Tariq Amir Bilal Hamza Ibrahim Mustafa Rami Samir Zaid "
             "Faisal Nabil Walid Adel",
        "f": "Aisha Fatima Leila Yasmin Noor Mariam Sara Layla Rania Hana Dina Salma Zainab Amira Nadia Huda "
             "Lina Rana Farah Maya",
        "mother": "Amina Khadija Samira Nawal Fatma Najwa Soraya Mona Wafa Hanan Souad Zahra Ghada Nour Iman "
                  "Rasha Samar",
        "last": "Haddad Khalil Mansour Nasser Saleh Aziz Hamdan Rahman Hussein Mahmoud Darwish Sayed Abboud "
                "Hamid Qureshi Malik Jaber Khoury Haidar Yassin Barakat Suleiman Rashid Saadeh",
    },
    "hispanic": {
        "m": "Mateo Diego Carlos Javier Luis Miguel Alejandro Santiago Andres Pablo Juan Jose Sergio Fernando "
             "Ricardo Emilio Rafael Manuel Eduardo Hector",
        "f": "Sofia Valentina Camila Lucia Isabella Gabriela Daniela Mariana Paula Ana Carmen Elena Andrea "
             "Natalia Ximena Valeria Adriana Lorena Paola Rosa",
        "mother": "Maria Guadalupe Patricia Teresa Silvia Gloria Beatriz Marisol Leticia Veronica Alicia "
                  "Graciela Yolanda Norma Martha Rocio Elvira",
        "last": "Garcia Rodriguez Martinez Hernandez Lopez Gonzalez Perez Sanchez Ramirez Torres Flores Rivera "
                "Gomez Diaz Morales Reyes Cruz Ortiz Gutierrez Chavez Ramos Vargas Castillo Jimenez Romero",
    },
    "african": {
        "m": "Kwame Kofi Chinedu Emeka Tunde Femi Musa Abdi Yonas Dawit Kamau Juma Obinna Ikenna Segun Chidi "
             "Kwabena Tendai Sipho Jabari",
        "f": "Amara Chioma Ngozi Adaeze Funmi Yewande Abena Ama Akosua Halima Hodan Selam Hiwot Wanjiru Zawadi "
             "Nia Imani Ifeoma Folake Kemi",
        "mother": "Bisi Comfort Blessing Funmilayo Adaobi Yetunde Abebech Almaz Wambui Nkechi Efua Akua Mulu "
                  "Tsehay Achieng Nneka Ayodele",
        "last": "Okafor Okeke Adeyemi Mensah Owusu Boateng Asante Abebe Bekele Haile Kamau Mwangi Otieno "
                "Ochieng Nwosu Eze Obi Balogun Adebayo Olawale Danso Appiah Tesfaye Girma Kariuki",
    },
}
NAMES: Dict[str, Dict[str, List[str]]] = {s: {k: v.split() for k, v in d.items()} for s, d in _NAMES.items()}
NAME_POOL_KEYS = {"first_name": ("m", "f"), "mother_name": ("mother",), "last_name": ("last",)}


def norm(s: str) -> str:
    """lower case, accents stripped, single spaces, no punctuation around words."""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.replace(".", " ").replace("'", "").replace("-", " ")
    return " ".join(s.lower().split())


def name_style(cat: str, value: str, prefer: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
    """(style, gender) of a name in our lists; gender is "m"/"f" (first names), "f" for mothers,
    None for last names. (None, None) if we don't know the name. `prefer` breaks ties."""
    v = norm(value)
    hits: List[Tuple[str, Optional[str]]] = []
    for style, pools in NAMES.items():
        if cat == "first_name":
            for g in ("m", "f"):
                if v in {norm(x) for x in pools[g]}:
                    hits.append((style, g))
            if v in {norm(x) for x in pools["mother"]}:
                hits.append((style, "f"))
        elif cat == "mother_name":
            if v in {norm(x) for x in pools["mother"] + pools["f"]}:
                hits.append((style, "f"))
        elif cat == "last_name":
            if v in {norm(x) for x in pools["last"]}:
                hits.append((style, None))
    if not hits:
        return None, None
    for h in hits:
        if h[0] == prefer:
            return h
    return hits[0]


def _name_pool(cat: str, style: str, gender: Optional[str]) -> List[str]:
    p = NAMES[style]
    if cat == "first_name":
        return p["m"] if gender == "m" else p["f"] if gender == "f" else p["m"] + p["f"]
    if cat == "mother_name":
        return p["mother"]
    return p["last"]


def _ok_word(x: str, true: str, ex: set) -> bool:
    nx, nt = norm(x), norm(true)
    return nx not in ex and nx != nt and nx not in nt and nt not in nx


def _sample_similar(pool: Sequence[str], true: str, n: int, rng: random.Random) -> List[str]:
    """Prefer similar length (+-2 letters) so the true word doesn't stand out by size."""
    pool = list(dict.fromkeys(pool))
    close = [x for x in pool if abs(len(x) - len(true)) <= 2]
    pick = close if len(close) >= n else pool
    return rng.sample(pick, n)


def name_foils(cat: str, true: str, rng: random.Random, n: int, exclude: Iterable[str] = (),
               style: str = "anglo", gender: Optional[str] = None) -> List[str]:
    ex = {norm(x) for x in exclude}
    pool = [x for x in _name_pool(cat, style, gender) if _ok_word(x, true, ex)]
    if len(pool) < n and cat == "mother_name":            # top up from the same style's women's names
        pool += [x for x in NAMES[style]["f"] if _ok_word(x, true, ex)]
    if len(pool) < n:
        raise ValueError(f"not enough {style} alternatives for {cat}")
    return _sample_similar(pool, true, n, rng)


def random_name(cat: str, rng: random.Random, exclude: Iterable[str] = (), style: str = "anglo",
                gender: Optional[str] = None) -> str:
    ex = {norm(x) for x in exclude}
    return rng.choice([x for x in _name_pool(cat, style, gender) if norm(x) not in ex])


# ---------------------------------------------------------------------- cities
# name | region | lat | lon | tier   (tier: 3 = major metro, 2 = city, 1 = town; relative to the country)
_CITIES = {
    "CA": """
Edmonton|AB|53.546|-113.494|3
Calgary|AB|51.045|-114.058|3
Red Deer|AB|52.268|-113.811|2
Lethbridge|AB|49.694|-112.842|2
St. Albert|AB|53.633|-113.626|2
Medicine Hat|AB|50.041|-110.677|2
Grande Prairie|AB|55.171|-118.795|2
Airdrie|AB|51.292|-114.014|2
Fort McMurray|AB|56.726|-111.381|2
Sherwood Park|AB|53.517|-113.318|2
Spruce Grove|AB|53.545|-113.900|1
Leduc|AB|53.264|-113.551|1
Fort Saskatchewan|AB|53.713|-113.213|1
Okotoks|AB|50.725|-113.975|1
Cochrane|AB|51.189|-114.467|1
Camrose|AB|53.017|-112.835|1
Lloydminster|AB|53.278|-110.005|1
Brooks|AB|50.565|-111.899|1
Canmore|AB|51.089|-115.359|1
Wetaskiwin|AB|52.969|-113.377|1
Stony Plain|AB|53.526|-114.008|1
Beaumont|AB|53.357|-113.415|1
Chestermere|AB|51.050|-113.823|1
Cold Lake|AB|54.464|-110.182|1
High River|AB|50.580|-113.871|1
Lacombe|AB|52.468|-113.737|1
Sylvan Lake|AB|52.309|-114.096|1
Strathmore|AB|51.038|-113.400|1
Whitecourt|AB|54.143|-115.683|1
Hinton|AB|53.400|-117.585|1
Drumheller|AB|51.464|-112.711|1
Athabasca|AB|54.719|-113.286|1
Slave Lake|AB|55.283|-114.771|1
Peace River|AB|56.232|-117.290|1
Olds|AB|51.792|-114.106|1
Taber|AB|49.785|-112.151|1
Vegreville|AB|53.494|-112.052|1
Edson|AB|53.581|-116.439|1
Bonnyville|AB|54.268|-110.735|1
Vancouver|BC|49.283|-123.121|3
Surrey|BC|49.191|-122.849|3
Burnaby|BC|49.249|-122.980|2
Richmond|BC|49.166|-123.134|2
Abbotsford|BC|49.050|-122.304|2
Coquitlam|BC|49.284|-122.794|2
Langley|BC|49.104|-122.660|2
Kelowna|BC|49.888|-119.496|2
Victoria|BC|48.428|-123.365|2
Kamloops|BC|50.674|-120.327|2
Nanaimo|BC|49.166|-123.940|2
Prince George|BC|53.917|-122.750|2
Chilliwack|BC|49.158|-121.951|2
Vernon|BC|50.267|-119.272|1
Penticton|BC|49.491|-119.589|1
Courtenay|BC|49.687|-124.994|1
Cranbrook|BC|49.512|-115.769|1
Fort St. John|BC|56.252|-120.847|1
Prince Rupert|BC|54.315|-130.320|1
Nelson|BC|49.493|-117.294|1
Squamish|BC|49.701|-123.156|1
Williams Lake|BC|52.141|-122.141|1
Saskatoon|SK|52.133|-106.670|2
Regina|SK|50.445|-104.619|2
Prince Albert|SK|53.203|-105.753|1
Moose Jaw|SK|50.393|-105.551|1
Swift Current|SK|50.288|-107.794|1
Yorkton|SK|51.213|-102.462|1
North Battleford|SK|52.757|-108.286|1
Estevan|SK|49.140|-102.986|1
Weyburn|SK|49.661|-103.853|1
Winnipeg|MB|49.895|-97.138|3
Brandon|MB|49.848|-99.950|2
Steinbach|MB|49.526|-96.684|1
Thompson|MB|55.744|-97.855|1
Portage la Prairie|MB|49.973|-98.292|1
Winkler|MB|49.182|-97.941|1
Selkirk|MB|50.144|-96.884|1
Dauphin|MB|51.149|-100.050|1
Toronto|ON|43.653|-79.383|3
Ottawa|ON|45.421|-75.697|3
Mississauga|ON|43.589|-79.644|3
Brampton|ON|43.731|-79.762|3
Hamilton|ON|43.256|-79.869|3
London|ON|42.984|-81.245|2
Markham|ON|43.856|-79.337|2
Vaughan|ON|43.837|-79.508|2
Kitchener|ON|43.452|-80.493|2
Waterloo|ON|43.464|-80.520|2
Windsor|ON|42.315|-83.036|2
Oakville|ON|43.467|-79.688|2
Burlington|ON|43.326|-79.799|2
Oshawa|ON|43.897|-78.866|2
Barrie|ON|44.389|-79.690|2
Guelph|ON|43.545|-80.248|2
Kingston|ON|44.231|-76.486|2
Sudbury|ON|46.522|-80.953|2
Thunder Bay|ON|48.381|-89.247|2
St. Catharines|ON|43.159|-79.247|2
Cambridge|ON|43.360|-80.312|2
Peterborough|ON|44.309|-78.320|1
Sarnia|ON|42.975|-82.404|1
Sault Ste. Marie|ON|46.522|-84.346|1
North Bay|ON|46.309|-79.461|1
Belleville|ON|44.162|-77.383|1
Niagara Falls|ON|43.090|-79.085|1
Timmins|ON|48.477|-81.330|1
Cornwall|ON|45.021|-74.730|1
Montreal|QC|45.502|-73.567|3
Quebec City|QC|46.813|-71.208|3
Laval|QC|45.607|-73.712|2
Gatineau|QC|45.477|-75.701|2
Longueuil|QC|45.531|-73.518|2
Sherbrooke|QC|45.404|-71.893|2
Saguenay|QC|48.428|-71.068|2
Trois-Rivieres|QC|46.343|-72.543|2
Levis|QC|46.803|-71.178|2
Drummondville|QC|45.883|-72.484|1
Rimouski|QC|48.449|-68.524|1
Granby|QC|45.400|-72.733|1
Halifax|NS|44.649|-63.575|2
Sydney|NS|46.136|-60.194|1
Truro|NS|45.365|-63.280|1
New Glasgow|NS|45.588|-62.645|1
Kentville|NS|45.077|-64.494|1
Moncton|NB|46.088|-64.778|2
Saint John|NB|45.273|-66.063|2
Fredericton|NB|45.964|-66.643|2
Bathurst|NB|47.619|-65.651|1
Miramichi|NB|47.029|-65.501|1
St. John's|NL|47.561|-52.713|2
Corner Brook|NL|48.951|-57.952|1
Gander|NL|48.954|-54.609|1
Charlottetown|PE|46.238|-63.131|1
Summerside|PE|46.394|-63.788|1
Whitehorse|YT|60.721|-135.057|1
Yellowknife|NT|62.454|-114.372|1
Iqaluit|NU|63.749|-68.522|1
""",
    "IN": """
Ludhiana|PB|30.901|75.857|2
Amritsar|PB|31.634|74.872|2
Jalandhar|PB|31.326|75.576|2
Patiala|PB|30.340|76.386|2
Bathinda|PB|30.211|74.945|1
Mohali|PB|30.704|76.717|1
Pathankot|PB|32.274|75.652|1
Hoshiarpur|PB|31.532|75.917|1
Moga|PB|30.817|75.171|1
Phagwara|PB|31.224|75.771|1
Firozpur|PB|30.925|74.613|1
Kapurthala|PB|31.380|75.384|1
Chandigarh|CH|30.733|76.779|2
Gurugram|HR|28.459|77.027|2
Faridabad|HR|28.408|77.317|2
Panipat|HR|29.391|76.964|1
Ambala|HR|30.378|76.776|1
Karnal|HR|29.686|76.990|1
Rohtak|HR|28.895|76.607|1
Hisar|HR|29.149|75.722|1
Sonipat|HR|28.993|77.016|1
Panchkula|HR|30.695|76.854|1
Kurukshetra|HR|29.969|76.878|1
Yamunanagar|HR|30.129|77.268|1
Delhi|DL|28.614|77.209|3
Lucknow|UP|26.847|80.947|2
Kanpur|UP|26.449|80.332|2
Agra|UP|27.176|78.008|2
Varanasi|UP|25.318|82.974|2
Noida|UP|28.535|77.391|2
Ghaziabad|UP|28.669|77.454|2
Meerut|UP|28.984|77.706|2
Prayagraj|UP|25.436|81.846|2
Bareilly|UP|28.367|79.430|1
Aligarh|UP|27.898|78.088|1
Gorakhpur|UP|26.760|83.373|1
Moradabad|UP|28.839|78.776|1
Jhansi|UP|25.448|78.569|1
Mathura|UP|27.492|77.674|1
Dehradun|UK|30.316|78.032|1
Haridwar|UK|29.946|78.164|1
Rishikesh|UK|30.087|78.268|1
Haldwani|UK|29.218|79.513|1
Roorkee|UK|29.854|77.888|1
Nainital|UK|29.380|79.464|1
Shimla|HP|31.105|77.173|1
Dharamshala|HP|32.219|76.323|1
Mandi|HP|31.708|76.932|1
Solan|HP|30.905|77.097|1
Kullu|HP|31.958|77.109|1
Srinagar|JK|34.084|74.797|2
Jammu|JK|32.727|74.857|2
Jaipur|RJ|26.912|75.787|2
Jodhpur|RJ|26.238|73.024|2
Kota|RJ|25.213|75.865|2
Udaipur|RJ|24.585|73.712|1
Ajmer|RJ|26.450|74.640|1
Bikaner|RJ|28.022|73.312|1
Alwar|RJ|27.553|76.635|1
Ahmedabad|GJ|23.023|72.571|3
Surat|GJ|21.170|72.831|3
Vadodara|GJ|22.307|73.181|2
Rajkot|GJ|22.304|70.802|2
Bhavnagar|GJ|21.765|72.152|1
Jamnagar|GJ|22.471|70.058|1
Gandhinagar|GJ|23.216|72.637|1
Anand|GJ|22.556|72.951|1
Mumbai|MH|19.076|72.878|3
Pune|MH|18.520|73.857|3
Nagpur|MH|21.146|79.088|2
Nashik|MH|19.998|73.790|2
Aurangabad|MH|19.877|75.343|2
Thane|MH|19.218|72.978|2
Navi Mumbai|MH|19.033|73.030|2
Solapur|MH|17.660|75.906|1
Kolhapur|MH|16.705|74.243|1
Amravati|MH|20.937|77.780|1
Bengaluru|KA|12.972|77.594|3
Mysuru|KA|12.296|76.639|2
Mangaluru|KA|12.914|74.856|2
Hubballi|KA|15.365|75.124|2
Belagavi|KA|15.850|74.498|1
Davangere|KA|14.464|75.922|1
Chennai|TN|13.083|80.271|3
Coimbatore|TN|11.017|76.956|2
Madurai|TN|9.925|78.120|2
Tiruchirappalli|TN|10.791|78.705|2
Salem|TN|11.665|78.146|1
Tirunelveli|TN|8.714|77.757|1
Vellore|TN|12.917|79.133|1
Hyderabad|TG|17.385|78.487|3
Warangal|TG|17.969|79.594|2
Nizamabad|TG|18.673|78.094|1
Karimnagar|TG|18.439|79.129|1
Visakhapatnam|AP|17.687|83.218|2
Vijayawada|AP|16.506|80.648|2
Guntur|AP|16.307|80.437|1
Nellore|AP|14.443|79.987|1
Tirupati|AP|13.629|79.419|1
Kochi|KL|9.931|76.267|2
Thiruvananthapuram|KL|8.524|76.937|2
Kozhikode|KL|11.259|75.780|2
Thrissur|KL|10.527|76.214|1
Kollam|KL|8.893|76.614|1
Kannur|KL|11.875|75.373|1
Kolkata|WB|22.573|88.364|3
Howrah|WB|22.596|88.264|2
Durgapur|WB|23.520|87.312|1
Asansol|WB|23.684|86.983|1
Siliguri|WB|26.727|88.395|1
Indore|MP|22.720|75.858|2
Bhopal|MP|23.260|77.413|2
Jabalpur|MP|23.181|79.986|2
Gwalior|MP|26.218|78.183|2
Ujjain|MP|23.180|75.785|1
Patna|BR|25.594|85.138|2
Gaya|BR|24.796|85.008|1
Bhagalpur|BR|25.244|86.972|1
Muzaffarpur|BR|26.120|85.385|1
Bhubaneswar|OR|20.296|85.825|2
Cuttack|OR|20.463|85.883|1
Rourkela|OR|22.260|84.854|1
Guwahati|AS|26.144|91.736|2
Silchar|AS|24.833|92.779|1
Dibrugarh|AS|27.472|94.912|1
Ranchi|JH|23.344|85.310|2
Jamshedpur|JH|22.805|86.203|2
Dhanbad|JH|23.796|86.430|1
Raipur|CG|21.251|81.630|2
Bhilai|CG|21.209|81.429|1
Panaji|GA|15.490|73.828|1
""",
    "PK": """
Karachi|PK|24.861|67.010|3
Lahore|PK|31.520|74.359|3
Islamabad|PK|33.684|73.048|2
Rawalpindi|PK|33.598|73.044|2
Faisalabad|PK|31.418|73.079|2
Multan|PK|30.158|71.525|2
Peshawar|PK|34.015|71.525|2
Quetta|PK|30.180|66.975|2
Gujranwala|PK|32.187|74.195|2
Sialkot|PK|32.494|74.531|1
Bahawalpur|PK|29.395|71.683|1
Sargodha|PK|32.083|72.671|1
""",
    "BD": """
Dhaka|BD|23.810|90.413|3
Chittagong|BD|22.357|91.784|2
Khulna|BD|22.846|89.540|2
Sylhet|BD|24.895|91.869|1
Rajshahi|BD|24.374|88.604|1
Comilla|BD|23.461|91.185|1
Barisal|BD|22.701|90.354|1
Rangpur|BD|25.744|89.275|1
""",
    "LK": """
Colombo|LK|6.927|79.861|2
Kandy|LK|7.291|80.634|1
Galle|LK|6.053|80.221|1
Jaffna|LK|9.661|80.026|1
Negombo|LK|7.209|79.835|1
Trincomalee|LK|8.587|81.215|1
Anuradhapura|LK|8.312|80.403|1
Batticaloa|LK|7.731|81.674|1
""",
    "NP": """
Kathmandu|NP|27.717|85.324|2
Pokhara|NP|28.210|83.986|1
Biratnagar|NP|26.452|87.272|1
Bharatpur|NP|27.683|84.433|1
Birgunj|NP|27.012|84.877|1
Dharan|NP|26.812|87.284|1
Butwal|NP|27.700|83.448|1
Nepalgunj|NP|28.050|81.617|1
""",
    "PH": """
Manila|PH|14.600|120.984|3
Cebu City|PH|10.316|123.885|2
Davao City|PH|7.190|125.455|2
Iloilo City|PH|10.720|122.562|1
Baguio|PH|16.402|120.596|1
Cagayan de Oro|PH|8.454|124.632|1
Bacolod|PH|10.676|122.951|1
Zamboanga City|PH|6.921|122.079|1
Angeles City|PH|15.145|120.589|1
General Santos|PH|6.116|125.172|1
""",
    "CN": """
Beijing|CN|39.904|116.407|3
Shanghai|CN|31.230|121.474|3
Guangzhou|CN|23.129|113.264|3
Shenzhen|CN|22.543|114.058|3
Chengdu|CN|30.573|104.066|3
Wuhan|CN|30.593|114.305|3
Hangzhou|CN|30.274|120.155|3
Xi'an|CN|34.341|108.940|3
Nanjing|CN|32.060|118.797|3
Tianjin|CN|39.343|117.362|3
Chongqing|CN|29.563|106.551|3
Hong Kong|CN|22.320|114.170|3
""",
    "KR": """
Seoul|KR|37.567|126.978|3
Busan|KR|35.180|129.076|3
Incheon|KR|37.456|126.705|2
Daegu|KR|35.871|128.602|2
Daejeon|KR|36.351|127.385|2
Gwangju|KR|35.160|126.852|2
Ulsan|KR|35.538|129.311|2
Suwon|KR|37.264|127.029|2
""",
    "JP": """
Tokyo|JP|35.676|139.650|3
Osaka|JP|34.694|135.502|3
Yokohama|JP|35.444|139.638|3
Nagoya|JP|35.181|136.906|3
Sapporo|JP|43.062|141.354|3
Fukuoka|JP|33.590|130.402|3
Kobe|JP|34.690|135.196|2
Kyoto|JP|35.012|135.768|2
Hiroshima|JP|34.385|132.455|2
Sendai|JP|38.268|140.870|2
""",
    "VN": """
Ho Chi Minh City|VN|10.823|106.630|3
Hanoi|VN|21.028|105.834|3
Da Nang|VN|16.054|108.202|2
Hai Phong|VN|20.845|106.688|2
Can Tho|VN|10.045|105.747|2
Hue|VN|16.464|107.590|1
Nha Trang|VN|12.239|109.197|1
Vung Tau|VN|10.346|107.084|1
""",
    "NG": """
Lagos|NG|6.524|3.379|3
Kano|NG|12.002|8.592|3
Ibadan|NG|7.378|3.947|3
Abuja|NG|9.076|7.399|2
Port Harcourt|NG|4.816|7.050|2
Benin City|NG|6.335|5.627|2
Enugu|NG|6.459|7.549|2
Kaduna|NG|10.510|7.416|2
Onitsha|NG|6.145|6.788|2
Owerri|NG|5.485|7.035|1
Abeokuta|NG|7.146|3.361|1
Jos|NG|9.896|8.858|1
""",
    "GH": """
Accra|GH|5.604|-0.187|3
Kumasi|GH|6.688|-1.624|2
Tamale|GH|9.403|-0.842|1
Takoradi|GH|4.899|-1.760|1
Cape Coast|GH|5.106|-1.247|1
Sunyani|GH|7.339|-2.327|1
Ho|GH|6.601|0.470|1
Koforidua|GH|6.094|-0.259|1
""",
    "KE": """
Nairobi|KE|-1.292|36.822|3
Mombasa|KE|-4.043|39.668|2
Kisumu|KE|-0.092|34.768|1
Nakuru|KE|-0.303|36.080|1
Eldoret|KE|0.514|35.270|1
Thika|KE|-1.033|37.069|1
Malindi|KE|-3.219|40.117|1
Kitale|KE|1.016|35.006|1
""",
    "ET": """
Addis Ababa|ET|9.030|38.740|3
Dire Dawa|ET|9.593|41.866|1
Mekelle|ET|13.497|39.475|1
Gondar|ET|12.600|37.467|1
Bahir Dar|ET|11.594|37.390|1
Hawassa|ET|7.062|38.476|1
Jimma|ET|7.674|36.835|1
Adama|ET|8.540|39.270|1
""",
    "MX": """
Mexico City|MX|19.433|-99.133|3
Guadalajara|MX|20.660|-103.350|3
Monterrey|MX|25.686|-100.316|3
Puebla|MX|19.041|-98.206|2
Tijuana|MX|32.515|-117.038|2
Leon|MX|21.122|-101.682|2
Merida|MX|20.967|-89.623|2
Queretaro|MX|20.588|-100.389|2
Cancun|MX|21.162|-86.851|1
Oaxaca|MX|17.073|-96.726|1
""",
    "US": """
New York|US|40.713|-74.006|3
Los Angeles|US|34.052|-118.244|3
Chicago|US|41.878|-87.630|3
Houston|US|29.760|-95.370|3
Seattle|US|47.606|-122.332|3
San Francisco|US|37.775|-122.419|3
Boston|US|42.360|-71.059|3
Dallas|US|32.777|-96.797|3
Phoenix|US|33.448|-112.074|3
Denver|US|39.739|-104.990|3
Atlanta|US|33.749|-84.388|3
Miami|US|25.762|-80.192|3
Detroit|US|42.331|-83.046|3
Minneapolis|US|44.978|-93.265|2
Portland|US|45.515|-122.679|2
Spokane|US|47.659|-117.426|1
Boise|US|43.615|-116.202|1
Billings|US|45.783|-108.501|1
Fargo|US|46.877|-96.790|1
Great Falls|US|47.506|-111.300|1
""",
    "GB": """
London|GB|51.507|-0.128|3
Manchester|GB|53.481|-2.243|3
Birmingham|GB|52.486|-1.890|3
Leeds|GB|53.801|-1.549|2
Glasgow|GB|55.864|-4.252|2
Liverpool|GB|53.408|-2.991|2
Edinburgh|GB|55.953|-3.188|2
Bristol|GB|51.455|-2.588|2
Sheffield|GB|53.381|-1.470|2
Leicester|GB|52.637|-1.140|2
Cardiff|GB|51.482|-3.179|2
Belfast|GB|54.597|-5.930|2
Nottingham|GB|52.954|-1.158|2
""",
    "AE": """
Dubai|AE|25.205|55.271|3
Abu Dhabi|AE|24.454|54.377|2
Sharjah|AE|25.346|55.421|2
Al Ain|AE|24.207|55.745|1
Ras Al Khaimah|AE|25.800|55.976|1
Fujairah|AE|25.129|56.326|1
""",
    "SA": """
Riyadh|SA|24.713|46.675|3
Jeddah|SA|21.485|39.193|3
Mecca|SA|21.389|39.858|2
Medina|SA|24.525|39.569|2
Dammam|SA|26.392|49.977|2
Taif|SA|21.270|40.416|1
Tabuk|SA|28.383|36.567|1
Abha|SA|18.216|42.505|1
""",
    "EG": """
Cairo|EG|30.044|31.236|3
Alexandria|EG|31.200|29.919|3
Port Said|EG|31.265|32.302|1
Suez|EG|29.967|32.550|1
Luxor|EG|25.687|32.640|1
Aswan|EG|24.089|32.900|1
Mansoura|EG|31.041|31.378|1
Tanta|EG|30.786|31.000|1
""",
    "IR": """
Tehran|IR|35.689|51.389|3
Mashhad|IR|36.297|59.606|3
Isfahan|IR|32.655|51.668|2
Shiraz|IR|29.592|52.584|2
Tabriz|IR|38.080|46.292|2
Ahvaz|IR|31.318|48.671|2
Qom|IR|34.640|50.876|2
Kermanshah|IR|34.314|47.065|1
""",
}
COUNTRIES = {"CA": "Canada", "IN": "India", "PK": "Pakistan", "BD": "Bangladesh", "LK": "Sri Lanka",
             "NP": "Nepal", "PH": "Philippines", "CN": "China", "KR": "South Korea", "JP": "Japan",
             "VN": "Vietnam", "NG": "Nigeria", "GH": "Ghana", "KE": "Kenya", "ET": "Ethiopia", "MX": "Mexico",
             "US": "United States", "GB": "United Kingdom", "AE": "United Arab Emirates", "SA": "Saudi Arabia",
             "EG": "Egypt", "IR": "Iran"}
COUNTRY_ALIASES = {"usa": "US", "us": "US", "america": "US", "uk": "GB", "england": "GB", "scotland": "GB",
                   "britain": "GB", "uae": "AE", "korea": "KR", "hong kong": "CN"}
REGIONS = {  # province / state names -> code (only within CA and IN, the countries we split by region)
    "CA": {"AB": "Alberta", "BC": "British Columbia", "SK": "Saskatchewan", "MB": "Manitoba", "ON": "Ontario",
           "QC": "Quebec", "NS": "Nova Scotia", "NB": "New Brunswick", "NL": "Newfoundland and Labrador",
           "PE": "Prince Edward Island", "YT": "Yukon", "NT": "Northwest Territories", "NU": "Nunavut"},
    "IN": {"PB": "Punjab", "CH": "Chandigarh", "HR": "Haryana", "DL": "Delhi", "UP": "Uttar Pradesh",
           "UK": "Uttarakhand", "HP": "Himachal Pradesh", "JK": "Jammu and Kashmir", "RJ": "Rajasthan",
           "GJ": "Gujarat", "MH": "Maharashtra", "KA": "Karnataka", "TN": "Tamil Nadu", "TG": "Telangana",
           "AP": "Andhra Pradesh", "KL": "Kerala", "WB": "West Bengal", "MP": "Madhya Pradesh", "BR": "Bihar",
           "OR": "Odisha", "AS": "Assam", "JH": "Jharkhand", "CG": "Chhattisgarh", "GA": "Goa"},
}
CITY_ALIASES = {"new delhi": "Delhi", "bangalore": "Bengaluru", "bombay": "Mumbai", "calcutta": "Kolkata",
                "madras": "Chennai", "gurgaon": "Gurugram", "mysore": "Mysuru", "mangalore": "Mangaluru",
                "allahabad": "Prayagraj", "trivandrum": "Thiruvananthapuram", "cochin": "Kochi",
                "vizag": "Visakhapatnam", "trichy": "Tiruchirappalli", "baroda": "Vadodara", "poona": "Pune",
                "hubli": "Hubballi", "belgaum": "Belagavi", "saigon": "Ho Chi Minh City", "nyc": "New York",
                "new york city": "New York", "quebec": "Quebec City", "st albert": "St. Albert",
                "saint albert": "St. Albert", "fort mcmurray": "Fort McMurray", "ft mcmurray": "Fort McMurray",
                "ft saskatchewan": "Fort Saskatchewan", "st catharines": "St. Catharines",
                "st johns": "St. John's", "saint johns": "St. John's", "chittagong": "Chittagong",
                "chattogram": "Chittagong", "bengaluru": "Bengaluru", "kitchener waterloo": "Kitchener"}


@dataclass(frozen=True)
class City:
    name: str
    country: str
    region: str
    lat: float
    lon: float
    tier: int


CITIES: List[City] = []
for _cc, _block in _CITIES.items():
    for _line in _block.strip().splitlines():
        _n, _r, _la, _lo, _t = _line.split("|")
        CITIES.append(City(_n, _cc, _r, float(_la), float(_lo), int(_t)))


def km(a: City, b: City) -> float:
    p1, p2 = math.radians(a.lat), math.radians(b.lat)
    dp, dl = p2 - p1, math.radians(b.lon - a.lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


def parse_place(text: str) -> Tuple[Optional[str], Optional[str]]:
    """'Alberta, Canada' / 'AB' / 'Punjab' / 'India' -> (country, region); None where unknown."""
    country = region = None
    reg_country = None
    for part in [norm(p) for p in text.replace("/", ",").split(",") if p.strip()]:
        if part in COUNTRY_ALIASES:                  # checked first: "UK" = United Kingdom
            country = COUNTRY_ALIASES[part]
            continue
        hit = [(cc, code) for cc, regs in REGIONS.items() for code, name in regs.items()
               if part in (norm(code), norm(name))]
        if hit:
            reg_country, region = hit[0]
            continue
        cc = next((cc for cc, name in COUNTRIES.items() if part in (norm(name), norm(cc))), None)
        if cc:
            country = cc
    if region is not None:
        if country is None:
            country = reg_country
        elif country != reg_country:                 # e.g. "Punjab, Pakistan": the country wins
            region = None
    return country, region


def find_city(text: str) -> List[City]:
    """Every city in our table matching the typed text. 'Edmonton, AB' / 'Edmonton, Alberta' narrow it."""
    name, _, where = text.partition(",")
    key = norm(name)
    key = norm(CITY_ALIASES.get(key, key))
    hits = [c for c in CITIES if norm(c.name) == key]
    if where.strip() and len(hits) > 1:
        cc, reg = parse_place(where)
        narrowed = [c for c in hits if (cc is None or c.country == cc) and (reg is None or c.region == reg)]
        hits = narrowed or hits
    return hits


def city_display(text: str) -> str:
    """What to show for the true hometown: the person's own spelling (that's what they recognize),
    without a trailing ', Province'."""
    return " ".join(text.partition(",")[0].split()).title().replace("'S", "'s")


def city_foils(true: str, rng: random.Random, n: int, exclude: Iterable[str] = (),
               home: Optional[City] = None, country: Optional[str] = None, region: Optional[str] = None,
               min_km: float = 60.0, shortlist: int = 10) -> List[str]:
    """Same province/state first, similar size, > min_km away, nearest first (a random n of the
    nearest `shortlist`). Without coordinates (a town we don't have) the same region/country is used."""
    ex = {norm(x) for x in exclude} | {norm(true)}
    if home is not None:
        country, region = home.country, home.region
    if country is None:
        raise ValueError("unknown place: need a country")
    pool = [c for c in CITIES if c.country == country and norm(c.name) not in ex and norm(c.name) not in norm(true)]
    if home is not None:
        pool = [c for c in pool if km(home, c) > min_km and abs(c.tier - home.tier) <= 1]
    tier = home.tier if home else 1

    def key(c: City):
        same = 0 if (region is None or c.region == region) else 1
        return (same, km(home, c) if home else abs(c.tier - tier) + rng.random())

    pool.sort(key=key)
    same_region = [c for c in pool if region is None or c.region == region]
    short = pool[:max(shortlist, n)] if len(same_region) < n else same_region[:max(shortlist, n)]
    if len(short) < n:
        raise ValueError(f"not enough cities near {true!r} in our list")
    return [c.name for c in rng.sample(short, n)]


def random_city(rng: random.Random, exclude: Iterable[str] = (), home: Optional[City] = None,
                country: str = "CA", region: Optional[str] = None, min_km: float = 60.0) -> str:
    """Control condition: a hometown the person has never lived in, from the same kind of place."""
    ex = {norm(x) for x in exclude}
    if home is not None:
        country, region = home.country, home.region
    pool = [c for c in CITIES if c.country == country and norm(c.name) not in ex
            and (home is None or (km(home, c) > min_km and abs(c.tier - home.tier) <= 1))]
    local = [c for c in pool if region is None or c.region == region]
    if len(local) >= 3:
        return rng.choice(local).name
    # small province: the nearest places elsewhere in the country (never the far side of it)
    if home is not None:
        pool.sort(key=lambda c: km(home, c))
        return rng.choice(pool[:8]).name
    return rng.choice(pool).name
