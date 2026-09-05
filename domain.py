from dataclasses import dataclass, asdict
from uuid import uuid4
@dataclass
class LedgerEntry:
    id:str; account:str; amount:float; currency:str="USD"; status:str="posted"
_entries={}
def post_entry(account:str, amount:float, currency:str="USD"):
    e=LedgerEntry(str(uuid4()),account,amount,currency); _entries[e.id]=e; return asdict(e)
def list_entries(): return [asdict(x) for x in _entries.values()]
