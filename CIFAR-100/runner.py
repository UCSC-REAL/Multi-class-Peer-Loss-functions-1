import numpy as np
import csv
import torch.nn.parallel
import torch.optim
import torch.utils.data
import pickle
from tqdm import tqdm
import torch.nn as nn
from model import *
from noise_data_cifar_100 import *
import torch
import torchvision
import torch.nn as nn
import torch.nn.functional as F
from torch.nn import init
from torch.autograd import Variable
import argparse
torch.autograd.set_detect_anomaly(True)
import math
num_classes = 100
num_epochs = 250

CUDA = True if torch.cuda.is_available() else False

Tensor = torch.cuda.FloatTensor if CUDA else torch.FloatTensor

device = torch.device("cuda:2" if torch.cuda.is_available() else "cpu")


opt = parser.parse_args()

# Stable CE
class CrossEntropyLossStable(nn.Module):
    def __init__(self, reduction='mean', eps=1e-5):
        super(CrossEntropyLossStable, self).__init__()
        self._name = "Stable Cross Entropy Loss"
        self._eps = eps
        self._softmax = nn.Softmax(dim=-1)
        self._nllloss = nn.NLLLoss(reduction=reduction)

    def forward(self, outputs, labels):
        return self._nllloss( torch.log( self._softmax(outputs) + self._eps ), labels )

        
criterion = CrossEntropyLossStable()
criterion.to(device)


# Training
def train(train_loader, peer_loader, model, optimizer, epoch):

    model.train()
    for i, (idx, input, target) in enumerate(train_loader):
        if idx.size(0) != batch_size:
            continue
        input = torch.autograd.Variable(input.to(device))
        target = torch.autograd.Variable(target.to(device))
        output = model(input)
        optimizer.zero_grad()
        
        if epoch < 100:
            loss = criterion(output, target)
    
        else:
            # Prepare mixmatched images and labels for the Peer Term
            peer_iter = iter(peer_loader)
            input1 = peer_iter.next()[1]
            output1 = model(input1.to(device))
            target2 = peer_iter.next()[2]
            target2 = torch.Tensor(target2.float())
            target2 = torch.autograd.Variable(target2.to(device))
            # Peer Loss with Cross-Entropy loss: L(f(x), y) - L(f(x1), y2)
            loss = criterion(output, target.long()) - f_alpha(epoch) * criterion(output1, target2.long())
        
        loss.to(device)
        loss.backward()
        optimizer.step()



def test(model, test_loader):
    model.eval()
    correct = 0
    total = 0

    for i, (idx, input, target) in enumerate(test_loader):
        input = torch.Tensor(input).to(device)
        target = torch.autograd.Variable(target).to(device)
        total += target.size(0)
        output = model(input)
        _, predicted = torch.max(output.detach(), 1)
        correct += predicted.eq(target).sum().item()
    accuracy = 100. * correct / total

    return accuracy

    
    # alpha list for the peer term
    if args.r == 0.1 or args.r == 0.2:
        alpha_threshold = [0.0, 0.0, 2.0, 10.0, 20.0]
        milestone = [0, 10, 30, 100, 150]
        alpha_list = []
        for i in range(len(milestone) - 1):
            count = milestone[i]
            a_ratio = (alpha_threshold[i + 1] - alpha_threshold[i]) / (milestone[i + 1] - milestone[i])
            while count < milestone[i + 1]:
                a = alpha_threshold[i] + (count - milestone[i] + 1) * a_ratio
                alpha_list.append(a)
                count += 1
    else:
         alpha_list = [0.95 for i in range(150)]

# The weight of peer term
def f_alpha(epoch):
    if args.r == 0.1 or args.r == 0.2:
        alpha1 = np.linspace(0.0, 0.0, num=110)
        alpha2 = np.linspace(0.0, 2, num=20)
        alpha3 = np.linspace(2, 10, num=70)
        alpha4 = np.linspace(10, 20, num=50)
     
        alpha = np.concatenate((alpha1, alpha2, alpha3, alpha4),axis=0)
    else:
        alpha1 = np.linspace(0.0, 0.0, num=100)
        alpha2 = np.linspace(0.95, 0.95, num=150)
     
        alpha = np.concatenate((alpha1, alpha2),axis=0)
        
    return alpha[epoch]
   
# Adjust learning rate and for SGD Optimizer
def adjust_learning_rate(optimizer, epoch, lr_plan):
    for param_group in optimizer.param_groups:
        param_group['lr'] = lr_plan[epoch]/(1+f_alpha(epoch))
        
        

def main(writer):
    model_PL = resnet_cifar18_pre(num_classes=100).to(device)
    best_val_acc = 0
    train_acc_result = []
    val_acc_noisy_result = []
    test_acc_result = []
    
    # Hyper-parameters: learning rate and the weight of peer term \alpha
    lr_list = [0.1] * 60 + [0.01] * 40 + [0.001] * 150
    
    
    # Dataloader for peer samples, which is used for the estimation of the marginal distribution
    peer_train = peer_data_train(batch_size=args.batchsize, img_size=(32, 32))
    peer_val = peer_data_val(batch_size=args.batchsize, img_size=(32, 32))
    
    for epoch in range(num_epochs):
        print("epoch=", epoch,'r=', args.r)
        learning_rate = lr_list[epoch]
        
        if epoch == 100:
            model_PL = torch.load('./trained_models/' + str(args.r) + '_' + str(args.s))
        
        # We adopted the SGD optimizer
        optimizer_PL = torch.optim.SGD(model_PL.parameters(), momentum=0.9, weight_decay=5e-4, lr=learning_rate)
        # asjust the learning rate
        adjust_learning_rate(optimizer_PL, epoch, lr_list)
        train(train_loader=train_loader_noisy, peer_loader = peer_train, model=model_PL, optimizer=optimizer_PL, epoch=epoch)
        print("validating model_PL...")
        
        # Training acc is calculated via noisy training data
        train_acc = test(model=model_PL, test_loader=train_loader_noisy)
        train_acc_result.append(train_acc)
        print('train_acc=', train_acc)
        
        # Validation acc is calculated via noisy validation data
        valid_acc = test(model=model_PL, test_loader=valid_loader_noisy)
        val_acc_noisy_result.append(valid_acc)
        print('valid_acc_noise=', valid_acc)
        
        # Calculate test accuracy
        test_acc = test(model=model_PL, test_loader=test_loader_)
        test_acc_result.append(test_acc)
        print('test_acc=', test_acc)
        
               
        # Best model is selected by referring to the accuracy of validation noisy
        
        if best_val_acc <= valid_acc:
            best_val_acc = valid_acc
            torch.save(model_PL, './trained_models/' + str(args.r) + '_' + str(args.s))
            print("saved, the accuracy of validation noisy increases.")
        
        writer.writerow([epoch, train_acc, valid_acc, test_acc])


def evaluate(path):
    model = torch.load(path)
    test_acc = test(model=model, test_loader=test_loader_)
    print('test_acc=', test_acc)



if __name__ == '__main__':
    
    # Save statistics
    print("Begin:")
    writer1 = csv.writer(open(f'result_{r}.csv','w'))
    writer1.writerow(['Epoch', 'Training Acc', 'Val_Noisy_Acc', 'Test_ACC'])
    os.makedirs("./trained_models/", exist_ok=True)
    
    main(writer1)
    evaluate('./trained_models/' + str(args.r) + '_' + str(args.s))
    print("Traning finished")
