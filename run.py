"""Local dev entrypoint: uvicorn src.food_rescue_router.api:app --reload"""
import uvicorn

if __name__ == "__main__":
    uvicorn.run("src.food_rescue_router.api:app", host="127.0.0.1", port=8787, reload=True)
