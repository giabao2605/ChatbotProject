"""One bounded graph operation selected by an authenticated request."""
from dataclasses import dataclass,asdict
import hashlib
import json
import re


def graph_fingerprint(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,default=str,separators=(',',':')).encode()).hexdigest()


@dataclass(frozen=True,slots=True)
class GraphQuery:
    root_key:str
    product_scope:str
    site:str
    direction:str='contains'
    max_relations:int=500
    max_generation_edges:int=50

    def __post_init__(self):
        if (type(self.max_relations) is not int or not 0<=self.max_relations<=500
                or type(self.max_generation_edges) is not int or not 0<=self.max_generation_edges<=50):
            raise ValueError('Graph bounds cannot exceed the v2 contract')
        if (any(not isinstance(value,str) or not value.strip() or len(value)>255
                for value in (self.root_key,self.product_scope,self.site))
                or self.direction not in {'contains','where_used'}):
            raise ValueError('Bounded graph context required')
        if self.direction=='contains':
            if not self.root_key.startswith('document:') or not self.root_key[9:].isdigit() or int(self.root_key[9:])<=0:
                raise ValueError('Positive document root required')
        elif not self.root_key.startswith('part:') or not self.root_key[5:].strip():
            raise ValueError('Explicit part root required')


def graph_query_context(query):
    defaults={'max_relations':500,'max_generation_edges':50}
    return {key:value for key,value in asdict(query).items() if key not in defaults or value!=defaults[key]}


@dataclass(frozen=True,slots=True)
class BomRollupQuery:
    graph:GraphQuery
    order_quantity:str|None
    rounding_scope:str
    quantity_scope:str|None=None

    def __post_init__(self):
        if (not isinstance(self.graph,GraphQuery) or self.graph.direction!='contains'
                or (self.order_quantity is None)==(self.quantity_scope is None)
                or (self.order_quantity is not None and (not isinstance(self.order_quantity,str) or len(self.order_quantity)>128
                    or not re.fullmatch(r'[0-9]+(?:\.[0-9]+)?',self.order_quantity)))
                or (self.quantity_scope is not None and (not isinstance(self.quantity_scope,str)
                    or not self.quantity_scope.strip() or len(self.quantity_scope)>255))
                or not isinstance(self.rounding_scope,str) or not self.rounding_scope.strip()
                or len(self.rounding_scope)>255):
            raise ValueError('Bounded BOM quantity and reviewed policy scope required')


def bom_rollup_context(query):
    return {**graph_query_context(query.graph), 'rounding_scope': query.rounding_scope,
            **({'quantity_scope': query.quantity_scope} if query.quantity_scope is not None
               else {'order_quantity': query.order_quantity})}


def bom_rollup_payload(query):
    """Preserve historical nested descriptors when the new basis is absent."""
    return {key:value for key,value in asdict(query).items() if value is not None}
