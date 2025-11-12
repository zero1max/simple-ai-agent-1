# main.py
"""
Yangilangan main.py
- .env ni o'qiydi
- ChatGoogleGenerativeAI (Gemini) bilan agent yaratadi
- Agentni ishga tushiradi va natijani Pydantic bilan parse qiladi
- Natijani output/leads.xlsx, output/leads.json, output/leads.csv ga saqlaydi
- save_tool funksiyasini chaqiradi (tools.py ichidagi save_to_txt)
"""

import os
from dotenv import load_dotenv
from pydantic import BaseModel
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import PydanticOutputParser
from langchain.agents import AgentExecutor, create_tool_calling_agent
from pathlib import Path
from datetime import datetime
import json
import csv
import pandas as pd
import traceback

# Sizning tools.py faylingizdan import
from tools import scrape_tool, search_tool, save_tool

# .env yuklash
load_dotenv()

# Xavfsizlik / diagnostika: GOOGLE_API_KEY yoki GOOGLE_APPLICATION_CREDENTIALS mavjudligini tekshirish
g_api_key = os.getenv("GOOGLE_API_KEY")
g_json = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")

if g_api_key:
    # print qilishda kalitni to'liq chiqarmang
    print("GOOGLE_API_KEY topildi:", f"{g_api_key[:6]}...{g_api_key[-4:]}")
    os.environ["GOOGLE_API_KEY"] = g_api_key
elif g_json:
    print("GOOGLE_APPLICATION_CREDENTIALS topildi:", g_json)
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = g_json
else:
    print("Diqqat: GOOGLE_API_KEY yoki GOOGLE_APPLICATION_CREDENTIALS topilmadi. Service account JSON yoki API key ni .env ga qo'shing.")
    # davom etishga ruxsat beramiz — lekin LLM yaratishda xato kelishi mumkin

# --- LLM yaratish (xatolikni tutib chiqamiz) ---
try:
    llm = ChatGoogleGenerativeAI(model="gemini-2.0-flash")
    print("ChatGoogleGenerativeAI muvaffaqiyatli yaratildi.")
except Exception as e:
    print("LLM yaratishda xatolik yuz berdi:")
    traceback.print_exc()
    raise

# Pydantic strukturalari
class LeadResponse(BaseModel):
    company: str
    contact_info: str
    email: str
    summary: str
    outreach_message: str
    tools_used: list[str]

class LeadResponseList(BaseModel):
    leads: list[LeadResponse]

# parser
parser = PydanticOutputParser(pydantic_object=LeadResponseList)

# prompt
prompt = ChatPromptTemplate.from_messages([
    ("system", """
    Sen sotuvchilar uchun yordamchi AI agentsan.

    TOPSHIRIQ:
    1. Uzbek tilida yoz barcha so'zlarni
    2. Toshkent shahridan IT xizmatlariga muhtoj 5 ta kichik biznesni top
    3. Har bir kompaniya uchun:
       - Kompaniya nomini yoz
       - Aloqa ma'lumotlarini top
       - Email manzilni top
       - Nima uchun ularga IT xizmati kerakligini tushuntir
       - Ularga murojaat xatini yoz

    4. Natijani shu formatda ber: {format_instructions}
    5. So'ngida 'save' toolidan foydalanib faylga saqla
    """),
    ("human", "{query}"),
    ("placeholder", "{agent_scratchpad}"),
]).partial(format_instructions=parser.get_format_instructions())

# tools ro'yxati (tools.py da e'lon qilingan obyektlar)
tools = [scrape_tool, search_tool, save_tool]

# agent
agent = create_tool_calling_agent(
    llm=llm,
    prompt=prompt,
    tools=tools
)

agent_executor = AgentExecutor(agent=agent, tools=tools, verbose=True)

# Topshiriq
query = "Toshkent shahridan IT xizmatlariga muhtoj 5 ta kichik kompaniyani top va tahlil qil"

# Agentni ishga tushirish
try:
    raw_response = agent_executor.invoke({"query": query})
    print("Agent raw response:", raw_response)
except Exception as e:
    print("Agent ishga tushishda xato yuz berdi:")
    traceback.print_exc()
    raise

# Parser va strukturaga o'tkazish
try:
    # raw_response.get('output') agentning chiqishini oladi; ba'zan to'g'ridan-to'g'ri str bo'ladi
    output_text = raw_response.get('output') if isinstance(raw_response, dict) else raw_response
    # Agar output ichida kod bloki bo'lsa (```json ...```), uni tozalash
    if isinstance(output_text, str) and output_text.strip().startswith("```"):
        # ```json\n{...}\n```
        # oson usul: suzib to'g'ri JSON qatorini topamiz
        cleaned = output_text.strip().strip("`")
        # Agar ichida hali ham ``` so'zlari bo'lsa, olib tashlaymiz
        cleaned = cleaned.strip()
        # Agar hali ham kod bloki ichida "json" so'zi bo'lsa, olib tashlang
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
        output_text = cleaned

    structured_response = parser.parse(output_text)  # type: ignore
    print("Parser orqali strukturaviy natija olindi.")
except Exception as e:
    print("Parser yoki parse bosqichida xatolik yuz berdi:")
    traceback.print_exc()
    raise

# OUTPUT papkasini tayyorlash
output_dir = Path("output")
output_dir.mkdir(exist_ok=True)

# JSON ga saqlash
json_path = output_dir / "leads.json"
try:
    with open(json_path, "w", encoding="utf-8") as jf:
        json.dump([lead.dict() for lead in structured_response.leads], jf, ensure_ascii=False, indent=2)
    print(f"Leads JSON ga saqlandi: {json_path}")
except Exception:
    print("JSON ga yozishda xatolik:")
    traceback.print_exc()

# CSV ga saqlash
csv_path = output_dir / "leads.csv"
try:
    with open(csv_path, "w", encoding="utf-8", newline="") as cf:
        writer = csv.writer(cf)
        writer.writerow(["company","contact_info","email","summary","outreach_message","tools_used","scraped_at"])
        for lead in structured_response.leads:
            writer.writerow([
                lead.company,
                lead.contact_info,
                lead.email,
                lead.summary,
                lead.outreach_message,
                ";".join(lead.tools_used) if lead.tools_used else "",
                datetime.now().isoformat(timespec="seconds")
            ])
    print(f"Leads CSV ga saqlandi: {csv_path}")
except Exception:
    print("CSV ga yozishda xatolik:")
    traceback.print_exc()

# Excel ga saqlash (append + dedup)
excel_path = output_dir / "leads.xlsx"
try:
    rows = []
    for lead in structured_response.leads:
        rows.append({
            "company": lead.company,
            "contact_info": lead.contact_info,
            "email": lead.email,
            "summary": lead.summary,
            "outreach_message": lead.outreach_message,
            "tools_used": ";".join(lead.tools_used) if lead.tools_used else "",
            "scraped_at": datetime.now().isoformat(timespec="seconds")
        })
    df_new = pd.DataFrame(rows)

    if excel_path.exists():
        try:
            df_existing = pd.read_excel(excel_path)
            df_out = pd.concat([df_existing, df_new], ignore_index=True)
            # Deduplikatsiya: email yoki company bo'yicha takrorlarni olib tashlaymiz
            if "email" in df_out.columns:
                df_out.drop_duplicates(subset=["email"], keep="last", inplace=True)
            else:
                df_out.drop_duplicates(subset=["company"], keep="last", inplace=True)
        except Exception:
            # Agar eski faylni o'qishda muammo bo'lsa, faqat yangi yozamiz
            df_out = df_new
    else:
        df_out = df_new

    df_out.to_excel(excel_path, index=False)
    print(f"Leads Excel fayliga saqlandi: {excel_path}")
except Exception:
    print("Excel ga yozishda xatolik:")
    traceback.print_exc()

# save_tool (tools.py dagi save_to_txt) dan foydalanish — agent promptida ham so'ralgan edi
try:
    text_for_save = json.dumps([lead.dict() for lead in structured_response.leads], ensure_ascii=False, indent=2)
    # save_tool.func - Tool obyektida func nomi bilan saqlangan funksiyani chaqiramiz
    result_save = save_tool.func(text_for_save) # type: ignore
    print("save_tool natijasi:", result_save)
except Exception:
    print("save_tool chaqirishda xatolik:")
    traceback.print_exc()

print("Barchasi bajarildi. Output papkasini tekshiring (output/leads.xlsx, output/leads.json, output/leads.csv).")
