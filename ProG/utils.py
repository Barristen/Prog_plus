import os
import numpy as np
import random
import torch
from copy import deepcopy
from random import shuffle
from torch_geometric.data import Data
from torch_geometric.utils import subgraph, k_hop_subgraph
import pickle as pk
from torch_geometric.utils import to_undirected
from torch_geometric.loader.cluster import ClusterData
from torch import nn, optim
from torch_geometric.datasets import Planetoid
import torch.nn.functional as F
from torch_geometric.loader import NeighborSampler
from sklearn.metrics import accuracy_score
seed = 0


def seed_everything(seed):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)

    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def mkdir(path):
    folder = os.path.exists(path)
    if not folder:
        os.makedirs(path)
        print("create folder {}".format(path))
    else:
        print("folder exists! {}".format(path))


# used in pre_train.py
def gen_ran_output(data, model):
    vice_model = deepcopy(model)

    for (vice_name, vice_model_param), (name, param) in zip(vice_model.named_parameters(), model.named_parameters()):
        if vice_name.split('.')[0] == 'projection_head':
            vice_model_param.data = param.data
        else:
            vice_model_param.data = param.data + 0.1 * torch.normal(0, torch.ones_like(
                param.data) * param.data.std())
    z2 = vice_model.forward_cl(data.x, data.edge_index, data.batch)

    return z2


# used in pre_train.py
def load_data4pretrain(dataname='CiteSeer', num_parts=200):
    data = pk.load(open('../Dataset/{}/feature_reduced.data'.format(dataname), 'br'))
    print(data)

    x = data.x.detach()
    edge_index = data.edge_index
    edge_index = to_undirected(edge_index)
    data = Data(x=x, edge_index=edge_index)
    input_dim = data.x.shape[1]
    hid_dim = input_dim
    graph_list = list(ClusterData(data=data, num_parts=num_parts, save_dir='../Dataset/{}/'.format(dataname)))

    return graph_list, input_dim, hid_dim


# used in prompt.py
def act(x=None, act_type='leakyrelu'):
    if act_type == 'leakyrelu':
        if x is None:
            return torch.nn.LeakyReLU()
        else:
            return F.leaky_relu(x)
    elif act_type == 'tanh':
        if x is None:
            return torch.nn.Tanh()
        else:
            return torch.tanh(x)
        

def GPPT_load_data(dataset):
    if dataset in ['Cora', 'CiteSeer']:
        dataset = Planetoid(root='/tmp/'+dataset, name=dataset)
        data = dataset[0]

        features = data.x
        labels = data.y
        train_mask = data.train_mask
        val_mask = data.val_mask
        test_mask = data.test_mask
        in_feats = features.size(1)
        n_classes = len(torch.unique(labels))
        n_edges = data.edge_index.size(1) // 2

        return data, features, labels, train_mask, val_mask, test_mask, in_feats, n_classes, n_edges


def get_init_info(args,dataset,device):

    data,features,labels,train_mask,val_mask,test_mask,in_feats,n_classes,n_edges=GPPT_load_data(dataset)
    print("""----Data statistics------'
      #Edges %d
      #Classes %d
      #Train samples %d
      #Val samples %d
      #Test samples %d""" %
          (n_edges, n_classes,
           train_mask.int().sum().item(),
           val_mask.int().sum().item(),
           test_mask.int().sum().item()))
    
        
    features = features.to(device)
    labels = labels.to(device)
    train_mask = train_mask.to(device)
    val_mask = val_mask.to(device)
    test_mask = test_mask.to(device)
    print("use cuda:", args.gpu)

    train_nid = train_mask.nonzero().squeeze()
    val_nid = val_mask.nonzero().squeeze()
    test_nid = test_mask.nonzero().squeeze()
    # g = dgl.remove_self_loop(g)
    # n_edges = g.number_of_edges()

    return data,features,labels,in_feats,n_classes,train_nid,val_nid,test_nid,device





def evaluate(model, data, nid, batch_size, device,sample_list):
    valid_loader = NeighborSampler(data.edge_index, node_idx=nid, sizes=sample_list, batch_size=batch_size, shuffle=True,drop_last=False,num_workers=0)
    model.eval()
    predictions = []
    labels = []
    with torch.no_grad(): 
        for step, (batch_size, n_id, adjs) in enumerate(valid_loader):      
            adjs = [adj.to(device) for adj in adjs]
            # 获取节点特征
            batch_features = data.x[n_id].to(device)
            # 获取节点标签（对于目标节点）
            batch_labels = data.y[n_id[:batch_size]].to(device)
            temp = model(adjs, batch_features).argmax(1)

            labels.append(batch_labels.cpu().numpy())
            predictions.append(temp.cpu().numpy())

        predictions = np.concatenate(predictions)
        labels = np.concatenate(labels)
        accuracy = accuracy_score(labels, predictions)
    return accuracy
    
def seed_torch(seed=1029):
	random.seed(seed)
	os.environ['PYTHONHASHSEED'] = str(seed) # 为了禁止hash随机化，使得实验可复现
	np.random.seed(seed)
	torch.manual_seed(seed)
	torch.cuda.manual_seed(seed)
	torch.cuda.manual_seed_all(seed) # if you are using multi-GPU.
	torch.backends.cudnn.benchmark = False
	torch.backends.cudnn.deterministic = True


def constraint(device,prompt):
    if isinstance(prompt,list):
        sum=0
        for p in prompt:
            sum=sum+torch.norm(torch.mm(p,p.T)-torch.eye(p.shape[0]).to(device))
        return sum/len(prompt)
    else:
        return torch.norm(torch.mm(prompt,prompt.T)-torch.eye(prompt.shape[0]).to(device))


def __seeds_list__(nodes):
    split_size = max(5, int(nodes.shape[0] / 400))
    seeds_list = list(torch.split(nodes, split_size))
    if len(seeds_list) < 400:
        print('len(seeds_list): {} <400, start overlapped split'.format(len(seeds_list)))
        seeds_list = []
        while len(seeds_list) < 400:
            split_size = random.randint(3, 5)
            seeds_list_1 = torch.split(nodes, split_size)
            seeds_list = seeds_list + list(seeds_list_1)
            nodes = nodes[torch.randperm(nodes.shape[0])]
    shuffle(seeds_list)
    seeds_list = seeds_list[0:400]

    return seeds_list


def __dname__(p, task_id):
    if p == 0:
        dname = 'task{}.meta.train.support'.format(task_id)
    elif p == 1:
        dname = 'task{}.meta.train.query'.format(task_id)
    elif p == 2:
        dname = 'task{}.meta.test.support'.format(task_id)
    elif p == 3:
        dname = 'task{}.meta.test.query'.format(task_id)
    else:
        raise KeyError

    return dname


def __pos_neg_nodes__(labeled_nodes, node_labels, i: int):
    pos_nodes = labeled_nodes[node_labels[:, i] == 1]
    pos_nodes = pos_nodes[torch.randperm(pos_nodes.shape[0])]
    neg_nodes = labeled_nodes[node_labels[:, i] == 0]
    neg_nodes = neg_nodes[torch.randperm(neg_nodes.shape[0])]
    return pos_nodes, neg_nodes


def __induced_graph_list_for_graphs__(seeds_list, label, p, num_nodes, potential_nodes, ori_x, same_label_edge_index,
                                      smallest_size, largest_size):
    seeds_part_list = seeds_list[p * 100:(p + 1) * 100]
    induced_graph_list = []
    for seeds in seeds_part_list:

        subset, _, _, _ = k_hop_subgraph(node_idx=torch.flatten(seeds), num_hops=1, num_nodes=num_nodes,
                                         edge_index=same_label_edge_index, relabel_nodes=True)

        temp_hop = 1
        while len(subset) < smallest_size and temp_hop < 5:
            temp_hop = temp_hop + 1
            subset, _, _, _ = k_hop_subgraph(node_idx=torch.flatten(seeds), num_hops=temp_hop, num_nodes=num_nodes,
                                             edge_index=same_label_edge_index, relabel_nodes=True)

        if len(subset) < smallest_size:
            need_node_num = smallest_size - len(subset)
            candidate_nodes = torch.from_numpy(np.setdiff1d(potential_nodes.numpy(), subset.numpy()))

            candidate_nodes = candidate_nodes[torch.randperm(candidate_nodes.shape[0])][0:need_node_num]

            subset = torch.cat([torch.flatten(subset), torch.flatten(candidate_nodes)])

        if len(subset) > largest_size:
            # directly downmsample
            subset = subset[torch.randperm(subset.shape[0])][0:largest_size - len(seeds)]
            subset = torch.unique(torch.cat([torch.flatten(seeds), subset]))

        sub_edge_index, _ = subgraph(subset, same_label_edge_index, num_nodes=num_nodes, relabel_nodes=True)

        x = ori_x[subset]
        graph = Data(x=x, edge_index=sub_edge_index, y=label)
        induced_graph_list.append(graph)

    return induced_graph_list


def graph_views(data, aug='random', aug_ratio=0.1):
    if aug == 'dropN':
        data = drop_nodes(data, aug_ratio)

    elif aug == 'permE':
        data = permute_edges(data, aug_ratio)
    elif aug == 'maskN':
        data = mask_nodes(data, aug_ratio)
    elif aug == 'random':
        n = np.random.randint(2)
        if n == 0:
            data = drop_nodes(data, aug_ratio)
        elif n == 1:
            data = permute_edges(data, aug_ratio)
        else:
            print('augmentation error')
            assert False
    return data


def drop_nodes(data, aug_ratio):
    node_num, _ = data.x.size()
    _, edge_num = data.edge_index.size()
    drop_num = int(node_num * aug_ratio)

    idx_perm = np.random.permutation(node_num)

    idx_drop = idx_perm[:drop_num]
    idx_nondrop = idx_perm[drop_num:]
    idx_nondrop.sort()
    idx_dict = {idx_nondrop[n]: n for n in list(range(idx_nondrop.shape[0]))}

    edge_index = data.edge_index.numpy()

    edge_index = [[idx_dict[edge_index[0, n]], idx_dict[edge_index[1, n]]] for n in range(edge_num) if
                  (not edge_index[0, n] in idx_drop) and (not edge_index[1, n] in idx_drop)]
    try:
        data.edge_index = torch.tensor(edge_index).transpose_(0, 1)
        data.x = data.x[idx_nondrop]
    except:
        data = data

    return data


def permute_edges(data, aug_ratio):
    """
    only change edge_index, all the other keys unchanged and consistent
    """
    node_num, _ = data.x.size()
    _, edge_num = data.edge_index.size()
    permute_num = int(edge_num * aug_ratio)
    edge_index = data.edge_index.numpy()

    idx_delete = np.random.choice(edge_num, (edge_num - permute_num), replace=False)
    data.edge_index = data.edge_index[:, idx_delete]

    return data


def mask_nodes(data, aug_ratio):
    node_num, feat_dim = data.x.size()
    mask_num = int(node_num * aug_ratio)

    token = data.x.mean(dim=0)
    idx_mask = np.random.choice(node_num, mask_num, replace=False)
    data.x[idx_mask] = token.clone().detach()

    return data


def GPPT_load_data(dataset):
    if dataset in ['Cora', 'CiteSeer']:
        dataset = Planetoid(root='/tmp/'+dataset, name=dataset)
        data = dataset[0]

        features = data.x
        labels = data.y
        train_mask = data.train_mask
        val_mask = data.val_mask
        test_mask = data.test_mask
        in_feats = features.size(1)
        n_classes = len(torch.unique(labels))
        n_edges = data.edge_index.size(1) // 2

        return data, features, labels, train_mask, val_mask, test_mask, in_feats, n_classes, n_edges

def GPPT_evaluate(model, data, nid, batch_size, device,sample_list):
    valid_loader = NeighborSampler(data.edge_index, node_idx=nid, sizes=sample_list, batch_size=batch_size, shuffle=True,drop_last=False,num_workers=0)
    model.eval()
    predictions = []
    labels = []
    with torch.no_grad(): 
        for step, (batch_size, n_id, adjs) in enumerate(valid_loader):      
            adjs = [adj.to(device) for adj in adjs]
            # 获取节点特征
            batch_features = data.x[n_id].to(device)
            # 获取节点标签（对于目标节点）
            batch_labels = data.y[n_id[:batch_size]].to(device)
            temp = model(adjs, batch_features).argmax(1)

            labels.append(batch_labels.cpu().numpy())
            predictions.append(temp.cpu().numpy())

        predictions = np.concatenate(predictions)
        labels = np.concatenate(labels)
        accuracy = accuracy_score(labels, predictions)
    return accuracy
    
# def constraint(device,prompt):
#     if isinstance(prompt,list):
#         sum=0
#         for p in prompt:
#             sum=sum+torch.norm(torch.mm(p,p.T)-torch.eye(p.shape[0]).to(device))
#         return sum/len(prompt)
#     else:
#         return torch.norm(torch.mm(prompt,prompt.T)-torch.eye(prompt.shape[0]).to(device))
            
# def seed_torch(seed=1029):
# 	random.seed(seed)
# 	os.environ['PYTHONHASHSEED'] = str(seed) # 为了禁止hash随机化，使得实验可复现
# 	np.random.seed(seed)
# 	torch.manual_seed(seed)
# 	torch.cuda.manual_seed(seed)
# 	torch.cuda.manual_seed_all(seed) # if you are using multi-GPU.
# 	torch.backends.cudnn.benchmark = False
# 	torch.backends.cudnn.deterministic = True

class NegativeEdge:
    def __init__(self):
        """
        Randomly sample negative edges
        """
        pass

    def __call__(self, data):
        num_nodes = data.num_nodes
        num_edges = data.num_edges

        edge_set = set([str(data.edge_index[0,i].cpu().item()) + "," + str(data.edge_index[1,i].cpu().item()) for i in range(data.edge_index.shape[1])])

        redandunt_sample = torch.randint(0, num_nodes, (2,5*num_edges))
        sampled_ind = []
        sampled_edge_set = set([])
        for i in range(5*num_edges):
            node1 = redandunt_sample[0,i].cpu().item()
            node2 = redandunt_sample[1,i].cpu().item()
            edge_str = str(node1) + "," + str(node2)
            if not edge_str in edge_set and not edge_str in sampled_edge_set and not node1 == node2:
                sampled_edge_set.add(edge_str)
                sampled_ind.append(i)
            if len(sampled_ind) == num_edges/2:
                break

        data.negative_edge_index = redandunt_sample[:,sampled_ind]
        
        return data
