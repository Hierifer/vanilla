import json
import os
from collections import OrderedDict

class LRUCache:
    def __init__(self, capacity: int, useCache: bool = True, cache_file: str = "cache/lru_cache.json"):
        self.cache = OrderedDict()
        self.capacity = capacity
        self.cache_file = cache_file
        self.useCache = useCache
        if self.useCache:
            self._load_from_file()

    def _load_from_file(self):
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, "r", encoding="utf-8") as f:
                    keys = json.load(f)
                    for key in keys:
                        self.cache[key] = True
                        # 保持容量限制
                        if len(self.cache) > self.capacity:
                            self.cache.popitem(last=False)
            except Exception as e:
                print(f"Error loading cache from {self.cache_file}: {e}")

    def sync(self):
        """将当前 cache 写入文件"""
        try:
            os.makedirs(os.path.dirname(self.cache_file), exist_ok=True)
            with open(self.cache_file, "w", encoding="utf-8") as f:
                # 保存为列表以保持顺序
                json.dump(list(self.cache.keys()), f)
                print("Cache synced to ", self.cache_file, ' at ', os.path.getmtime(self.cache_file))
        except Exception as e:
            print(f"Error syncing cache to {self.cache_file}: {e}")

    def get(self, key: str) -> bool:
        if key not in self.cache:
            return False
        self.cache.move_to_end(key)
        return True

    def put(self, key: str) -> None:
        if key in self.cache:
            self.cache.move_to_end(key)
        self.cache[key] = True
        if len(self.cache) > self.capacity:
            self.cache.popitem(last=False)
        # 每次写入都同步，保证数据不丢失
        self.sync()
