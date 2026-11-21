"""被代办人记在哪里：家庭服务时间账。

模块分工：
- ``data``：读取与结构校验；
- ``burden``：日记内的角色时间与缺失区间；
- ``allocation``：同车/公共行程的分摊与家庭总量不变性；
- ``itinerary``：在两种安排下为同一份日记构造合法替代行程；
- ``compare``：总体、角色、受保护组与未完成量的比较；
- ``calendar``：跨日与整次行程的日历视图；
- ``response``：未答复、晚回执与假设台账；
- ``report``：生成会议材料。
"""

__all__ = ["data", "burden", "allocation", "itinerary", "compare", "calendar", "response", "report"]
