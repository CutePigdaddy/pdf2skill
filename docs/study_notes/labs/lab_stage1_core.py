import yaml
class SimpleConfig:
  _instance = None
  def __new__(cls,config_path = None):
    if cls._instance is None:
      cls._instance = super().__new__(cls)
    return cls._instance
  
  def __init__(self,config_path = None) -> None:
    if config_path is not None:
      with open(config_path,'r') as f:
        self.config = yaml.safe_load(f)
    #对设置项进行进一步加工处理
    self.config = {"debug":True}

  def get(self,key):
    keys = self.config.keys()
    if key in keys:
      return self.config[key]
    else:
      raise KeyError("Key not found")
    
if __name__ == "__main__":
  c1 = SimpleConfig()
  c2 = SimpleConfig()
  print(c1 is c2)
  print(c1.get("debug"))