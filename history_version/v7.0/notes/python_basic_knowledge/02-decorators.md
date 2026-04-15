# 13 — Python 装饰器：为什么总是 `def -> def -> return`

**对应代码**：
- `app/pipeline/core/registry.py`
- `app/main.py`

## 读完本文你能回答

- Python 装饰器本质上是什么？
- 为什么有的装饰器只有一层 `def`，有的却是 `def -> def -> return`？
- `@registry.strategy("ocr")` 这一句到底等价于什么？
- 为什么模块一 `import`，这些注册装饰器就会自动执行？

---

## 先记住一句话

装饰器本质上就是：

> 接收一个对象，处理一下，再返回这个对象（或返回一个替代对象）的函数。

这个“对象”可以是：

- 函数
- 类
- 方法

---

## 1. 最简单的装饰器：一层 `def`

最简单的装饰器长这样：

```python
def deco(fn):
    print("装饰一下")
    return fn
```

使用：

```python
@deco
def hello():
    pass
```

它等价于：

```python
def hello():
    pass

hello = deco(hello)
```

所以：

- `@deco`
- 本质就是把下面定义出来的对象传给 `deco`

---

## 2. 类也可以被装饰

装饰器不只能装函数，也能装类。

例如：

```python
def mark(cls):
    cls.tag = "demo"
    return cls


@mark
class A:
    pass
```

等价于：

```python
class A:
    pass


A = mark(A)
```

所以你在项目里看到：

```python
def decorator(cls: type) -> type:
```

这里的 `cls` 就是“被装饰的类”。

---

## 3. 为什么项目里经常看到 `def -> def -> return`

因为项目里更常见的是：

**带参数的装饰器**

例如：

```python
@registry.strategy("ocr")
class OCRIngestionStrategy(BaseStrategy):
    ...
```

注意这里不是：

```python
@registry.strategy
```

而是：

```python
@registry.strategy("ocr")
```

这说明装饰器本身要先接收一个参数 `"ocr"`。

所以自然就会变成两层函数：

```python
def strategy(self, strategy_id: str):
    def decorator(cls: type) -> type:
        ...
        return cls
    return decorator
```

分工是：

- 外层 `def strategy(...)`
  先接收装饰器参数，例如 `"ocr"`
- 内层 `def decorator(cls)`
  再接收被装饰的类，例如 `OCRIngestionStrategy`

---

## 4. 一个最重要的等价变形

以后看到：

```python
@xxx
```

脑子里翻译成：

```python
obj = xxx(obj)
```

看到：

```python
@xxx(...)
```

脑子里翻译成：

```python
obj = xxx(...)(obj)
```

这句非常关键。

你现在看到的：

```python
@registry.strategy("ocr")
class OCRIngestionStrategy(BaseStrategy):
    ...
```

就等价于：

```python
class OCRIngestionStrategy(BaseStrategy):
    ...


OCRIngestionStrategy = registry.strategy("ocr")(OCRIngestionStrategy)
```

再拆一步：

```python
tmp = registry.strategy("ocr")   # 先得到一个真正的装饰器函数 decorator
OCRIngestionStrategy = tmp(OCRIngestionStrategy)
```

所以你看到的 `def -> def -> return`，本质上是：

> 先生成装饰器，再用装饰器处理对象。

---

## 5. 用你项目里的代码逐行翻译

代码：

```python
def strategy(self, strategy_id: str):
    """装饰器：注册 Strategy 实现类"""
    def decorator(cls: type) -> type:
        cls.strategy_id = strategy_id
        self._strategies[strategy_id] = cls
        return cls
    return decorator
```

逐行理解：

### 第一步：先收参数

```python
registry.strategy("ocr")
```

这里会执行外层函数：

```python
def strategy(self, strategy_id: str):
```

此时：

- `self` 是 `registry`
- `strategy_id` 是 `"ocr"`

外层函数最后返回一个内层函数：

```python
return decorator
```

### 第二步：再收类

随后 Python 会把类对象传进来：

```python
decorator(OCRIngestionStrategy)
```

也就是：

```python
def decorator(cls: type) -> type:
    cls.strategy_id = strategy_id
    self._strategies[strategy_id] = cls
    return cls
```

这一步做了三件事：

1. 给类加上：

```python
cls.strategy_id = "ocr"
```

2. 把类注册到字典里：

```python
registry._strategies["ocr"] = OCRIngestionStrategy
```

3. 把原类返回：

```python
return cls
```

---

## 6. 为什么最后一定要 `return cls`

因为装饰器最终会替换原来的名字绑定。

这句：

```python
@registry.strategy("ocr")
class OCRIngestionStrategy(BaseStrategy):
    ...
```

等价于：

```python
OCRIngestionStrategy = registry.strategy("ocr")(OCRIngestionStrategy)
```

如果不 `return cls`，那 `OCRIngestionStrategy` 这个名字最后可能就变成 `None` 或别的值了。

所以注册型装饰器通常都写成：

```python
def decorator(cls):
    # 做注册、做标记
    return cls
```

意思是：

- 我只做登记
- 但不改变这个类本身

---

## 7. 为什么项目里这种写法特别多

因为工程代码里的装饰器经常都要带参数，例如：

```python
@registry.stage("parser")
@registry.provider("openai_llm")
@registry.strategy("hybrid")
```

这些都需要一个“名字”或“ID”。

所以在真实项目里，反而是：

```python
def outer(...):
    def inner(obj):
        ...
        return obj
    return inner
```

这种结构更常见。

---

## 8. 为什么模块一 import，装饰器就自动执行

因为 Python 在导入模块时，会**执行模块顶层代码**。

例如这个模块：

```python
@registry.strategy("ocr")
class OCRIngestionStrategy(BaseStrategy):
    ...
```

当模块被 import 时，Python 会：

1. 先创建类对象 `OCRIngestionStrategy`
2. 立刻执行：

```python
registry.strategy("ocr")(OCRIngestionStrategy)
```

3. 所以注册动作就在 import 当下完成了

这也是为什么你们项目里 `main.py` 要主动 import 一堆模块：

```python
import app.pipeline.strategies.ingestion.ocr_strategy
```

目的不是“马上用这个模块”，而是：

> 触发装饰器执行，把类注册进 registry。

---

## 9. 你现在最容易混的点

### 误区 1：装饰器必须是两层函数

不是。

- 不带参数时，一层就够
- 带参数时，通常两层

### 误区 2：`@xxx(...)` 只是“调用一个函数”

不完整。

它其实是：

```python
先调用 xxx(...)
得到一个装饰器函数
再把对象传进去
```

### 误区 3：装饰器只能装函数

不是。

类也可以被装饰，项目里 `registry` 这套就是典型例子。

---

## 10. 一个超短模板

### 不带参数装饰器

```python
def deco(obj):
    # 做点事
    return obj
```

### 带参数装饰器

```python
def deco(arg):
    def wrapper(obj):
        # 用 arg 做点事
        return obj
    return wrapper
```

---

## 11. 放回当前项目，一句话记住

看到：

```python
@registry.strategy("ocr")
class OCRIngestionStrategy(BaseStrategy):
    ...
```

脑子里就翻译成：

```python
先用 "ocr" 生成一个装饰器
再把 OCRIngestionStrategy 这个类交给它
装饰器顺手把类注册到 registry 里
最后把类原样返回
```

---

## 12. 一句话总结

- 装饰器本质是“接收对象并返回对象”的函数
- `@xxx` 等价于 `obj = xxx(obj)`
- `@xxx(...)` 等价于 `obj = xxx(...)(obj)`
- 你看到的 `def -> def -> return`，本质是“带参数装饰器”的标准写法

如果后面你还想补 `@classmethod`、`@property`、函数包装器 `*args/**kwargs`，可以继续放在这个专题下面。
