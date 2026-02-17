#for now...  Backend    python main.py
# Front end: cd frontend && npm run dev



#  uvicorn main:app --reload


from pipeline import pipeline
import asyncio

if __name__ == "__main__":
    asyncio.run(pipeline())

